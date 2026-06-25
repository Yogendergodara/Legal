"""Pytest configuration — section-first pipeline."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_LEGAL_AI = Path(__file__).resolve().parents[3] / "Legal ai"
if _LEGAL_AI.is_dir() and str(_LEGAL_AI) not in sys.path:
    sys.path.insert(0, str(_LEGAL_AI))


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: requires Postgres pgvector")
    config.addinivalue_line("markers", "benchmark: live LLM benchmark (nightly)")
    config.addinivalue_line("markers", "routing_golden: deterministic routing golden gate (Phase R8)")


def _database_url() -> str:
    return os.environ.get(
        "TEST_DATABASE_URL",
        os.environ.get(
            "DATABASE_URL",
            "postgresql://legalai:legalai@localhost:5435/legalai_test",
        ),
    )


@pytest.fixture(scope="session", autouse=True)
def _database_url_for_tests():
    os.environ["DATABASE_URL"] = _database_url()
    os.environ.setdefault("DOCUMENT_STORE_BACKEND", "pgvector")
    os.environ.setdefault("RERANKER_BACKEND", "lexical")
    from document_core.config import get_settings as get_core_settings

    get_core_settings.cache_clear()


@pytest.fixture(autouse=True)
def _integration_pg_document_store(request):
    if "integration" not in request.node.keywords:
        yield
        return
    yield request.getfixturevalue("pg_document_store")


@pytest.fixture
def pg_document_store(pg_engine, database_url):
    from sqlalchemy import text

    from document_core.db.migrate import run_migrations
    from document_core.store.memory_store import reset_store, set_store
    from document_core.store.pgvector_store import PgVectorDocumentStore

    run_migrations(database_url)
    with pg_engine.begin() as conn:
        conn.execute(text("TRUNCATE document_chunks, document_canonical, policy_catalog_vectors, policy_documents CASCADE"))
    reset_store()
    pg_store = PgVectorDocumentStore(database_url, hybrid_alpha=0.5)
    set_store(pg_store)
    yield pg_store
    reset_store()


@pytest.fixture(scope="session")
def database_url() -> str:
    return _database_url()


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
def review_settings_defaults(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("REVIEW_POLICY_SCOPE", "request")
    monkeypatch.setenv("GUARD_PASS_ENABLED", "false")
    from review_agent.config import get_settings
    from review_agent.models import llm_gateway

    get_settings.cache_clear()
    llm_gateway.reset_llm_limiter()
