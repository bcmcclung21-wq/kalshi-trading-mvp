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

if DATABASE_URL.startswith("postgresql"):
    connect_args["options"] = "-c statement_timeout=30000 -c lock_timeout=5000 -c idle_in_transaction_session_timeout=60000"

engine = create_engine(DATABASE_URL, future=True, pool_pre_ping=True, pool_recycle=1800, pool_timeout=5, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True, class_=Session)


# Columns that may be missing from market_snapshots in older Postgres
# instances. Idempotent ALTERs run on every boot to keep the schema in
# sync with the ORM. Each entry: (column_name, sql_type)
_MARKET_SNAPSHOT_REPAIRS = [
    ("spread", "DOUBLE PRECISION DEFAULT 0.0"),
    ("imbalance", "DOUBLE PRECISION DEFAULT 0.0"),
    ("volatility", "DOUBLE PRECISION DEFAULT 0.0"),
    ("microprice", "DOUBLE PRECISION DEFAULT 0.0"),
    ("liquidity_score", "DOUBLE PRECISION DEFAULT 0.0"),
]

_ORDER_RECORDS_REPAIRS = [
    ("realized_pnl", "DOUBLE PRECISION DEFAULT 0.0"),
    ("settled_at", "TIMESTAMP WITH TIME ZONE"),
    ("features_json", "TEXT DEFAULT '{}'"),
    ("estimated_win_probability", "DOUBLE PRECISION DEFAULT 0.0"),
    ("brier_snapshot_json", "TEXT DEFAULT '{}'"),
    ("calibration_status", "TEXT DEFAULT 'ok'"),
]

_CASHOUT_ORDERS_REPAIRS = [
    ("original_order_id", "INTEGER"),
    ("ticker", "TEXT"),
    ("side", "TEXT DEFAULT 'SELL'"),
    ("cashout_type", "TEXT"),
    ("size", "DOUBLE PRECISION DEFAULT 0.0"),
    ("price", "DOUBLE PRECISION DEFAULT 0.0"),
    ("status", "TEXT DEFAULT 'pending'"),
    ("created_at", "TIMESTAMP WITH TIME ZONE DEFAULT NOW()"),
]

_AUDIT_RUNS_REPAIRS = [
    ("feature_breakdown_json", "TEXT DEFAULT '{}'"),
    ("calibration_json", "TEXT DEFAULT '{}'"),
    ("learning_summary_json", "TEXT DEFAULT '{}'"),
    ("rolling_brier", "DOUBLE PRECISION DEFAULT 0.0"),
    ("brier_threshold", "DOUBLE PRECISION DEFAULT 0.25"),
    ("trades_in_window", "INTEGER DEFAULT 0"),
]


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


def _repair_market_snapshots_schema(conn) -> list[str]:
    """Idempotently add any columns missing from market_snapshots.
    Returns list of columns that were added.
    """
    return _repair_table_schema(conn, "market_snapshots", _MARKET_SNAPSHOT_REPAIRS)


def _repair_table_schema(conn, table_name: str, repairs: list[tuple[str, str]]) -> list[str]:
    """Idempotently add any columns missing from the named table."""
    added: list[str] = []
    if engine.dialect.name != "postgresql":
        return added
    try:
        existing_cols = {row[0] for row in conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = :table_name"
        ), {"table_name": table_name})}
    except Exception as exc:
        logger.warning("schema_repair_inspect_failed table=%s err=%s", table_name, exc)
        return added

    if not existing_cols:
        return added

    for col_name, col_type in repairs:
        if col_name in existing_cols:
            continue
        try:
            conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {col_name} {col_type}"))
            added.append(col_name)
            logger.info("schema_repair_added table=%s column=%s type=%s", table_name, col_name, col_type)
        except (ProgrammingError, OperationalError) as exc:
            logger.warning("schema_repair_failed table=%s column=%s err=%s", table_name, col_name, exc)
    return added


def init_db() -> BootstrapResult:
    """Initialize DB. Honors SKIP_DB_BOOTSTRAP=1 to bypass entirely.
    Even when not skipped: no advisory lock, no alembic upgrade.
    Just create_all + idempotent column repairs."""
    import app.models  # noqa: F401

    if os.getenv("SKIP_DB_BOOTSTRAP", "").strip() in ("1", "true", "True", "yes"):
        logger.warning("init_db_skipped reason=SKIP_DB_BOOTSTRAP_env_set")
        # Still run the column-repair pass since this is the most common
        # source of column-missing errors after a partial migration.
        try:
            _ensure_connection(engine)
            with engine.begin() as conn:
                _repair_market_snapshots_schema(conn)
                _repair_table_schema(conn, "order_records", _ORDER_RECORDS_REPAIRS)
                _repair_table_schema(conn, "audit_runs", _AUDIT_RUNS_REPAIRS)
                _repair_table_schema(conn, "cashout_orders", _CASHOUT_ORDERS_REPAIRS)
        except Exception as exc:
            logger.warning("init_db_skipped_but_repair_failed err=%s", exc)
        return BootstrapResult(
            schema_version=_schema_version(),
            tables_missing=[],
            tables_created=[],
            migration_applied=False,
            bootstrap_duration_ms=0,
        )

    started = time.perf_counter()
    _ensure_connection(engine)

    with engine.begin() as conn:
        inspector = inspect(conn)
        expected = sorted(Base.metadata.tables.keys())
        existing = set(inspector.get_table_names())
        missing = [t for t in expected if t not in existing]
        created: list[str] = []
        if missing:
            Base.metadata.create_all(bind=conn, tables=[Base.metadata.tables[t] for t in missing], checkfirst=True)
            created = missing[:]

        # Repair pass: add any columns the ORM expects but Postgres lacks.
        _repair_market_snapshots_schema(conn)
        _repair_table_schema(conn, "order_records", _ORDER_RECORDS_REPAIRS)
        _repair_table_schema(conn, "audit_runs", _AUDIT_RUNS_REPAIRS)
        _repair_table_schema(conn, "cashout_orders", _CASHOUT_ORDERS_REPAIRS)

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
