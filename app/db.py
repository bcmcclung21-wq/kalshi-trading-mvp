import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

def _db_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL is not set")

    # Railway/Postgres URLs sometimes come through as postgres://
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg2://", 1)
    elif url.startswith("postgresql://") and "+psycopg2" not in url and "+psycopg" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)

    return url

def _create_engine():
    url = _db_url()
    try:
        return create_engine(
            url,
            future=True,
            pool_pre_ping=True,
        )
    except ModuleNotFoundError as exc:
        if exc.name == "psycopg2":
            raise RuntimeError(
                "PostgreSQL driver missing. Install psycopg2-binary and rebuild the container."
            ) from exc
        raise

engine = _create_engine()

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    future=True,
)

def init_db():
    from app.models import Base
    Base.metadata.create_all(bind=engine)