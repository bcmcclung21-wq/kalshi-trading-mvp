from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError, ProgrammingError, SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


@dataclass(frozen=True)
class BootstrapResult:
    schema_version: str | None
    tables_missing: list[str]
    tables_created: list[str]
    migration_applied: bool
    bootstrap_duration_ms: int


def _db_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        return "sqlite:///./app.db"
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg2://", 1)
    elif url.startswith("postgresql://") and "+psycopg2" not in url and "+psycopg" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


DATABASE_URL = _db_url()
connect_args: dict[str, object] = {"connect_timeout": 5} if DATABASE_URL.startswith("postgresql") else {}
if DATABASE_URL.startswith("sqlite"):
    connect_args["check_same_thread"] = False

# Statement timeout prevents any single SQL call from hanging the boot.
# 30 seconds is generous for a healthy migration, fatal for a deadlock.
if DATABASE_URL.startswith("postgresql"):
    connect_args["options"] = "-c statement_timeout=30000 -c lock_timeout=10000 -c idle_in_transaction_session_timeout=60000"

engine = create_engine(DATABASE_URL, future=True, pool_pre_ping=True, pool_recycle=1800, pool_timeout=5, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True, class_=Session)


LOCK_KEY = 842311
LOCK_ACQUIRE_MAX_WAIT_SEC = 30
LOCK_RETRY_INTERVAL_SEC = 2.0


@contextmanager
def session_scope() -> Session:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _ensure_connection(db_engine: Engine, retries: int = 8, sleep_s: float = 1.25) -> None:
    for attempt in range(1, retries + 1):
        try:
            with db_engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except OperationalError:
            if attempt == retries:
                raise
            logger.warning("db_connection_retry attempt=%d max=%d", attempt, retries)
            time.sleep(sleep_s)


def _diagnose_lock_holder() -> str:
    """Return PID + age info for whoever currently holds the bootstrap lock.
    Best-effort; returns 'unknown' on any failure.
    """
    try:
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT pid, application_name, state,
                       EXTRACT(EPOCH FROM (now() - state_change))::int AS state_age_sec,
                       EXTRACT(EPOCH FROM (now() - backend_start))::int AS backend_age_sec
                FROM pg_locks l
                JOIN pg_stat_activity a USING (pid)
                WHERE l.locktype = 'advisory'
                  AND l.objid = :k
                LIMIT 1
            """), {"k": LOCK_KEY}).mappings().first()
            if row:
                return f"pid={row['pid']} app={row['application_name']} state={row['state']} state_age_sec={row['state_age_sec']} backend_age_sec={row['backend_age_sec']}"
    except Exception as exc:
        return f"diag_failed:{exc}"
    return "unknown"


def _force_release_stale_lock(max_age_sec: int = 300) -> bool:
    """If the bootstrap advisory lock is held by a backend that has been
    idle/stuck for more than max_age_sec, terminate that backend so the
    lock releases. Returns True if a backend was terminated.
    """
    try:
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT a.pid,
                       EXTRACT(EPOCH FROM (now() - a.backend_start))::int AS backend_age_sec
                FROM pg_locks l
                JOIN pg_stat_activity a USING (pid)
                WHERE l.locktype = 'advisory'
                  AND l.objid = :k
                  AND a.pid <> pg_backend_pid()
                ORDER BY a.backend_start ASC
                LIMIT 1
            """), {"k": LOCK_KEY}).mappings().first()
            if not row:
                return False
            if row["backend_age_sec"] is None or row["backend_age_sec"] < max_age_sec:
                return False
            pid = int(row["pid"])
            logger.warning("force_releasing_stale_bootstrap_lock pid=%s age_sec=%s", pid, row["backend_age_sec"])
            conn.execute(text("SELECT pg_terminate_backend(:p)"), {"p": pid})
            return True
    except Exception as exc:
        logger.warning("force_release_lock_failed err=%s", exc)
    return False


def _acquire_bootstrap_lock(conn) -> bool:
    """Try to grab the advisory lock with timeout + diagnostic retry.
    Returns True on success, False if it could not be acquired after
    LOCK_ACQUIRE_MAX_WAIT_SEC. Uses pg_try_advisory_lock so we never
    block forever; if a stale lock is found, we kill its holder.
    """
    deadline = time.monotonic() + LOCK_ACQUIRE_MAX_WAIT_SEC
    attempts = 0
    while True:
        attempts += 1
        got = conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}).scalar()
        if got:
            logger.info("bootstrap_lock_acquired attempts=%d", attempts)
            return True
        holder = _diagnose_lock_holder()
        logger.warning("bootstrap_lock_busy attempt=%d holder=%s", attempts, holder)
        if time.monotonic() >= deadline:
            # Last-ditch: force-release if holder is older than 5 minutes.
            if _force_release_stale_lock(max_age_sec=300):
                got = conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}).scalar()
                if got:
                    logger.info("bootstrap_lock_acquired_after_force_release")
                    return True
            logger.error("bootstrap_lock_unacquirable attempts=%d holder=%s", attempts, holder)
            return False
        time.sleep(LOCK_RETRY_INTERVAL_SEC)


def _run_startup_migrations() -> bool:
    try:
        from alembic import command
        from alembic.config import Config
    except Exception:
        logger.warning("alembic_unavailable_falling_back_to_metadata")
        return False

    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")
    return True


def _schema_version() -> str | None:
    try:
        with engine.connect() as conn:
            value = conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).scalar_one_or_none()
            return str(value) if value else None
    except SQLAlchemyError:
        return None


def verify_required_tables(required: list[str]) -> list[str]:
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    return [t for t in required if t not in existing]


def init_db() -> BootstrapResult:
    import app.models  # noqa: F401

    started = time.perf_counter()
    _ensure_connection(engine)
    migration_applied = False

    # Pre-flight: if the lock is already held by a long-dead backend, kill it
    # before we even try. This handles the common "previous deploy crashed
    # mid-migration" case without making the new boot wait the full timeout.
    if engine.dialect.name == "postgresql":
        _force_release_stale_lock(max_age_sec=120)

    with engine.begin() as conn:
        lock_held = True
        if engine.dialect.name == "postgresql":
            lock_held = _acquire_bootstrap_lock(conn)
            if not lock_held:
                logger.warning("init_db_proceeding_without_lock degraded=true")
        try:
            try:
                migration_applied = _run_startup_migrations()
            except Exception as exc:
                logger.warning("alembic_upgrade_failed falling_back_to_metadata err=%s", exc)
                migration_applied = False
            inspector = inspect(conn)
            expected = sorted(Base.metadata.tables.keys())
            existing = set(inspector.get_table_names())
            missing = [t for t in expected if t not in existing]
            created: list[str] = []
            if missing:
                Base.metadata.create_all(bind=conn, tables=[Base.metadata.tables[t] for t in missing], checkfirst=True)
                created = missing[:]
            try:
                conn.execute(text("ALTER TABLE market_snapshots ALTER COLUMN title TYPE TEXT"))
                conn.execute(text("ALTER TABLE market_snapshots ALTER COLUMN subtitle TYPE TEXT"))
            except (ProgrammingError, OperationalError):
                logger.warning("market_snapshots_text_cast_skipped")
            duration = int((time.perf_counter() - started) * 1000)
            missing_after = verify_required_tables(expected)
            if missing_after:
                logger.warning("db_required_tables_missing tables=%s", missing_after)
            result = BootstrapResult(
                schema_version=_schema_version(),
                tables_missing=missing,
                tables_created=created,
                migration_applied=migration_applied,
                bootstrap_duration_ms=duration,
            )
        finally:
            if engine.dialect.name == "postgresql" and lock_held:
                try:
                    conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
                except Exception as exc:
                    logger.warning("bootstrap_lock_unlock_failed err=%s", exc)

    logger.info(
        "db_bootstrap_complete schema_version=%s tables_missing=%s tables_created=%s migration_applied=%s bootstrap_duration_ms=%d",
        result.schema_version,
        result.tables_missing,
        result.tables_created,
        result.migration_applied,
        result.bootstrap_duration_ms,
    )
    return result
