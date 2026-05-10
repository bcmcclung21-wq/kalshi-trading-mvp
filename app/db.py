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

# Postgres-level timeouts so no statement or lock can hang the process
# below the Python layer. statement_timeout aborts any single SQL after
# 30s; lock_timeout aborts any lock acquisition after 5s.
if DATABASE_URL.startswith("postgresql"):
    connect_args["options"] = "-c statement_timeout=30000 -c lock_timeout=5000 -c idle_in_transaction_session_timeout=60000"

engine = create_engine(DATABASE_URL, future=True, pool_pre_ping=True, pool_recycle=1800, pool_timeout=5, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True, class_=Session)


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


def _ensure_connection(db_engine: Engine, retries: int = 5, sleep_s: float = 1.0) -> None:
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


def _schema_version() -> str | None:
    try:
        with engine.connect() as conn:
            value = conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).scalar_one_or_none()
            return str(value) if value else None
    except SQLAlchemyError:
        return None


def verify_required_tables(required: list[str]) -> list[str]:
    try:
        inspector = inspect(engine)
        existing = set(inspector.get_table_names())
        return [t for t in required if t not in existing]
    except Exception as exc:
        logger.warning("verify_required_tables_failed err=%s", exc)
        return []


def init_db() -> BootstrapResult:
    """Initialize DB. Honors SKIP_DB_BOOTSTRAP=1 to bypass entirely."""
    import app.models  # noqa: F401

    if os.getenv("SKIP_DB_BOOTSTRAP", "").strip() in ("1", "true", "True", "yes"):
        logger.warning("init_db_skipped reason=SKIP_DB_BOOTSTRAP_env_set")
        return BootstrapResult(
            schema_version=_schema_version(),
            tables_missing=[],
            tables_created=[],
            migration_applied=False,
            bootstrap_duration_ms=0,
        )

    started = time.perf_counter()
    _ensure_connection(engine)

    # NO advisory lock. NO alembic.upgrade(). Just create_all on missing
    # tables, idempotent, with checkfirst. Statement timeouts above
    # guarantee no individual SQL can hang the boot.
    with engine.begin() as conn:
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

    result = BootstrapResult(
        schema_version=_schema_version(),
        tables_missing=missing,
        tables_created=created,
        migration_applied=False,
        bootstrap_duration_ms=duration,
    )
    logger.info(
        "db_bootstrap_complete schema_version=%s tables_missing=%s tables_created=%s bootstrap_duration_ms=%d",
        result.schema_version,
        result.tables_missing,
        result.tables_created,
        result.bootstrap_duration_ms,
    )
    return result
