"""Tests for per-review MCP search cache (PF-1D)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk, RetrievalHit, SearchRequest
from review_agent.config import ReviewSettings
from review_agent.services.mcp_search_cache import (
    cache_stats,
    clear_mcp_search_cache,
    get_cached_hits,
    make_search_cache_key,
    set_cached_hits,
)
from review_agent.services.multi_retrieval import retrieve_hybrid_attempt


def _hit() -> RetrievalHit:
    chunk = IndexedChunk(
        chunk_id="c1",
        document_id=uuid4(),
        tenant_id="t1",
        kind=DocumentKind.POLICY,
        chunk_role=ChunkRole.PARENT,
        section_id="1",
        section_path="1",
        title="Policy",
        text="text",
    )
    return RetrievalHit(parent_chunk=chunk, matched_child_ids=[], score=0.9)


def _request(**kwargs) -> SearchRequest:
    defaults = {
        "tenant_id": "t1",
        "query": "incident notification",
        "kind": DocumentKind.POLICY,
        "top_k": 20,
    }
    defaults.update(kwargs)
    return SearchRequest(**defaults)


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_mcp_search_cache()
    yield
    clear_mcp_search_cache()


def test_cache_hit_same_key():
    key = make_search_cache_key("recall", _request())
    set_cached_hits(key, [_hit()])
    cached = get_cached_hits(key)
    assert cached is not None
    assert len(cached) == 1
    stats = cache_stats()
    assert stats["mcp_cache_hits"] == 1
    assert stats["mcp_cache_misses"] == 1


def test_cache_miss_different_fence():
    doc_a, doc_b = uuid4(), uuid4()
    key_a = make_search_cache_key("recall", _request(document_ids=[doc_a]))
    key_b = make_search_cache_key("recall", _request(document_ids=[doc_b]))
    set_cached_hits(key_a, [_hit()])
    assert get_cached_hits(key_b) is None


def test_clear_between_reviews():
    key = make_search_cache_key("fts", _request())
    set_cached_hits(key, [_hit()])
    clear_mcp_search_cache()
    assert get_cached_hits(key) is None
    assert cache_stats()["mcp_cache_hits"] == 0


@pytest.mark.asyncio
async def test_retrieve_hybrid_uses_cache(monkeypatch):
    doc_id = uuid4()
    calls = {"recall": 0, "fts": 0}

    class _Client:
        async def search_policy_recall(self, _req):
            calls["recall"] += 1
            return [_hit()]

        async def search_policy_fts(self, _req):
            calls["fts"] += 1
            return []

        async def search_policy_by_categories(self, _req, categories):
            return []

    cfg = ReviewSettings(mcp_search_cache_enabled=True, retrieval_recall_top_k=20)
    from document_core.config import get_settings as get_core_settings

    core = get_core_settings()
    client = _Client()
    kwargs = dict(
        client=client,
        tenant_id="t1",
        query="incident",
        categories=[],
        contract_type=None,
        policy_type=None,
        filter_doc_ids=[doc_id],
        category_hard_filter=False,
        attempt_index=0,
        cfg=cfg,
        core=core,
    )
    await retrieve_hybrid_attempt(**kwargs)
    await retrieve_hybrid_attempt(**kwargs)
    assert calls["recall"] == 1
    assert calls["fts"] == 1
