import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()

def _db_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()

    # No Postgres configured -> use local SQLite fallback
    if not url:
        return "sqlite:///./app.db"

    # Normalize legacy postgres URLs if they come back later
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

def init_db():
    import app.models  # registers model metadata
    Base.metadata.create_all(bind=engine)