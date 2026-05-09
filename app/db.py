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

engine = create_engine(DATABASE_URL, future=True, pool_pre_ping=True, pool_recycle=1800, connect_args=connect_args)
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


def init_db() -> BootstrapResult:
    import app.models  # noqa: F401

    started = time.perf_counter()
    _ensure_connection(engine)
    migration_applied = False
    lock_key = 842311

    with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": lock_key})
        try:
            migration_applied = _run_startup_migrations()
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
                migration_applied=migration_applied,
                bootstrap_duration_ms=duration,
            )
        finally:
            if engine.dialect.name == "postgresql":
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": lock_key})

    logger.info(
        "db_bootstrap_complete schema_version=%s tables_missing=%s tables_created=%s migration_applied=%s bootstrap_duration_ms=%d",
        result.schema_version,
        result.tables_missing,
        result.tables_created,
        result.migration_applied,
        result.bootstrap_duration_ms,
    )
    return result
