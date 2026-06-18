"""PostgreSQL fixtures for platform tests that use document-mcp."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from document_core.db.migrate import run_migrations
from document_core.store.memory_store import reset_store, set_store
from document_core.store.pgvector_store import PgVectorDocumentStore

_PLATFORM_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "Legal ai"
    / "db"
    / "migrations"
    / "003_platform_session.sql"
)


def _apply_platform_migrations(engine) -> None:
    if not _PLATFORM_MIGRATION.is_file():
        pytest.skip(f"Platform migration missing: {_PLATFORM_MIGRATION}")
    sql = _PLATFORM_MIGRATION.read_text(encoding="utf-8")
    with engine.begin() as conn:
        conn.execute(text(sql))


@pytest.fixture(scope="session", autouse=True)
def _database_url_for_platform_tests():
    os.environ.setdefault(
        "DATABASE_URL",
        "postgresql://legalai:legalai@localhost:5435/legalai",
    )
    os.environ.setdefault("DOCUMENT_STORE_BACKEND", "pgvector")
    from document_core.config import get_settings as get_core_settings

    get_core_settings.cache_clear()


@pytest.fixture(scope="session")
def database_url() -> str:
    return os.environ["DATABASE_URL"]


@pytest.fixture(scope="session")
def pg_engine(database_url: str):
    try:
        run_migrations(database_url)
        engine = create_engine(database_url, future=True)
        _apply_platform_migrations(engine)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL unavailable at {database_url}: {exc}")
    yield engine
    engine.dispose()


@pytest.fixture
def platform_tables(pg_engine):
    with pg_engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE platform_session_turns, platform_sessions, platform_memory CASCADE"
            )
        )
    yield


@pytest.fixture(autouse=True)
def pg_document_store(pg_engine, database_url: str):
    from legal_ai_platform.container import reset_container

    with pg_engine.begin() as conn:
        conn.execute(text("TRUNCATE document_chunks, document_canonical, policy_documents CASCADE"))
    reset_store()
    reset_container()
    pg_store = PgVectorDocumentStore(database_url, hybrid_alpha=0.5)
    set_store(pg_store)
    yield pg_store
    reset_store()
    reset_container()
