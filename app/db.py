import os

from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()


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

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_engine(
    DATABASE_URL,
    future=True,
    pool_pre_ping=True,
    connect_args=connect_args,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    future=True,
)


def init_db() -> None:
    import app.models

    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE market_snapshots ALTER COLUMN title TYPE TEXT"))
        conn.execute(text("ALTER TABLE market_snapshots ALTER COLUMN subtitle TYPE TEXT"))
