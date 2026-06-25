"""PostgreSQL fixtures for document_core tests."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text

from document_core.db.migrate import run_migrations
from document_core.store.memory_store import reset_store, set_store
from document_core.store.pgvector_store import PgVectorDocumentStore

# CI-stable defaults: avoid loading cross-encoder models in unit tests.
os.environ.setdefault("RERANKER_BACKEND", "lexical")
os.environ.setdefault("CATEGORY_TAGGER_MODE", "keyword")
os.environ.setdefault("SEARCH_BACKEND", "lexical")


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: requires Postgres (TEST_DATABASE_URL)")


def pytest_collection_modifyitems(items):
    for item in items:
        fixturenames = getattr(item, "fixturenames", ()) or ()
        if "store" in fixturenames or "pg_engine" in fixturenames:
            item.add_marker(pytest.mark.integration)


def _database_url() -> str:
    return os.environ.get(
        "TEST_DATABASE_URL",
        os.environ.get(
            "DATABASE_URL",
            "postgresql://legalai:legalai@localhost:5435/legalai_test",
        ),
    )


@pytest.fixture(scope="session")
def database_url() -> str:
    return _database_url()


@pytest.fixture(scope="session")
def pg_engine(database_url: str):
    try:
        run_migrations(database_url)
        engine = create_engine(database_url, future=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL unavailable at {database_url}: {exc}")
    yield engine
    engine.dispose()


@pytest.fixture
def store(pg_engine, database_url: str):
    with pg_engine.begin() as conn:
        conn.execute(text("TRUNCATE document_chunks, document_canonical, policy_catalog_vectors, policy_documents CASCADE"))
    reset_store()
    pg_store = PgVectorDocumentStore(database_url, hybrid_alpha=0.5)
    set_store(pg_store)
    yield pg_store
    reset_store()
