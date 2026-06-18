"""Pytest configuration — default to lexical mode for deterministic e2e in CI."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def _database_url_for_tests():
    os.environ.setdefault(
        "DATABASE_URL",
        "postgresql://legalai:legalai@localhost:5435/legalai",
    )
    os.environ.setdefault("DOCUMENT_STORE_BACKEND", "pgvector")
    from document_core.config import get_settings as get_core_settings

    get_core_settings.cache_clear()


@pytest.fixture(autouse=True)
def pg_document_store(pg_engine, database_url):
    from sqlalchemy import text

    from document_core.db.migrate import run_migrations
    from document_core.store.memory_store import reset_store, set_store
    from document_core.store.pgvector_store import PgVectorDocumentStore

    run_migrations(database_url)
    with pg_engine.begin() as conn:
        conn.execute(text("TRUNCATE document_chunks, document_canonical, policy_documents CASCADE"))
    reset_store()
    pg_store = PgVectorDocumentStore(database_url, hybrid_alpha=0.5)
    set_store(pg_store)
    yield pg_store
    reset_store()


@pytest.fixture(scope="session")
def database_url() -> str:
    return os.environ["DATABASE_URL"]


@pytest.fixture(scope="session")
def pg_engine(database_url: str):
    from sqlalchemy import create_engine, text

    from document_core.db.migrate import run_migrations

    try:
        run_migrations(database_url)
        engine = create_engine(database_url, future=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL unavailable at {database_url}: {exc}")
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def compliance_lexical_for_ci(monkeypatch: pytest.MonkeyPatch):
    """E2E tests run without LLM unless explicitly overridden."""
    monkeypatch.setenv("COMPLIANCE_MODE", "lexical")
    monkeypatch.setenv("REVIEW_PLAN_MODE", "dynamic")
    monkeypatch.setenv("REVIEW_POLICY_SOURCE", "request")
    monkeypatch.setenv("REVIEW_PIPELINE_MODE", "legacy")
    from review_agent.config import get_settings

    get_settings.cache_clear()
