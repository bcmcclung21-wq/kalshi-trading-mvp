from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


def _db_url() -> str:
    if settings.database_url:
        return settings.database_url
    Path(settings.database_path).parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{settings.database_path}"


engine = create_engine(_db_url(), future=True, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
