"""Tests for SessionPostgresStore."""

from __future__ import annotations

import pytest

from legal_ai_platform.session import SessionPostgresStore, SessionService
from legal_ai_platform.session.models import Turn


@pytest.fixture
def postgres_store(database_url: str, platform_tables) -> SessionPostgresStore:
    return SessionPostgresStore(database_url, load_limit=500)


@pytest.fixture
def postgres_session_service(postgres_store: SessionPostgresStore) -> SessionService:
    return SessionService(postgres_store)


def test_postgres_load_or_create_new(postgres_session_service: SessionService):
    state = postgres_session_service.load_or_create("pg-thread-1", "tenant-a")
    assert state.thread_id == "pg-thread-1"
    assert state.turns == []


def test_postgres_persist_and_reload(postgres_session_service: SessionService):
    state = postgres_session_service.load_or_create("pg-thread-2", "tenant-a")
    postgres_session_service.append_user_turn(state, "Hello postgres")
    postgres_session_service.append_assistant_turn(
        state, content="Hi back", agent="review", task_type="review"
    )
    postgres_session_service.persist(state)

    reloaded = postgres_session_service.load_or_create("pg-thread-2", "tenant-a")
    assert len(reloaded.turns) == 2
    assert reloaded.turns[0].content == "Hello postgres"
    assert reloaded.turns[1].agent == "review"


def test_postgres_append_only_turns(postgres_store: SessionPostgresStore):
    from legal_ai_platform.session.models import SessionState

    state = SessionState(thread_id="pg-append", tenant_id="tenant-a")
    state.turns.append(Turn(role="user", content="one"))
    postgres_store.save(state)

    state.turns.append(Turn(role="user", content="two"))
    postgres_store.save(state)

    reloaded = postgres_store.load("tenant-a", "pg-append")
    assert reloaded is not None
    assert [t.content for t in reloaded.turns] == ["one", "two"]


def test_postgres_delete(postgres_session_service: SessionService):
    state = postgres_session_service.load_or_create("pg-del", "tenant-a")
    postgres_session_service.append_user_turn(state, "delete me")
    postgres_session_service.persist(state)
    assert postgres_session_service.get_session("tenant-a", "pg-del") is not None

    result = postgres_session_service.delete_session("tenant-a", "pg-del")
    assert result["deleted"] is True
    assert postgres_session_service.get_session("tenant-a", "pg-del") is None
