"""Database engine and session helpers."""

from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from db.models import Base


@lru_cache
def get_engine(database_url: str):
    return create_engine(database_url, pool_pre_ping=True)


def get_session_factory(database_url: str) -> sessionmaker[Session]:
    engine = get_engine(database_url)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@contextmanager
def get_session(database_url: str) -> Generator[Session, None, None]:
    factory = get_session_factory(database_url)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db(database_url: str) -> None:
    """Create all tables (dev/bootstrap; production uses migrations)."""
    engine = get_engine(database_url)
    Base.metadata.create_all(engine)
