"""Tests for PostgresMemoryStore (Phase 9B)."""

from __future__ import annotations

import pytest

from legal_ai_platform.session.memory_bridge import MemoryBridge
from legal_ai_platform.session.memory_postgres import PostgresMemoryStore
from legal_ai_platform.session.models import MatterSnapshot


@pytest.fixture
def memory_store(database_url: str, platform_tables) -> PostgresMemoryStore:
    return PostgresMemoryStore(database_url)


def test_memory_save_and_search(memory_store: PostgresMemoryStore):
    memory_store.save(
        title="Review: MSA [acme]",
        content="Liability cap mismatch for twelve months renewal",
        hook="[review][acme][thread-1] 1 finding",
        tenant_id="acme",
        thread_id="thread-1",
        agent="review",
    )
    hits = memory_store.search("acme", ["liability cap"], limit=5)
    assert len(hits) == 1
    assert "Liability" in hits[0]["content"]


@pytest.mark.asyncio
async def test_memory_bridge_uses_postgres(memory_store: PostgresMemoryStore):
    memory_store.save(
        title="Review: NDA [demo]",
        content="Confidentiality term exceeds policy limit",
        hook="[review][demo][t-9] finding",
        tenant_id="demo",
        thread_id="t-9",
        agent="review",
    )
    bridge = MemoryBridge(postgres_store=memory_store, max_hits=5)
    snippets, hits = await bridge.search(
        query="confidentiality",
        tenant_id="demo",
        task_type="review",
        matter=MatterSnapshot(),
    )
    assert hits
    assert "Confidentiality" in snippets
