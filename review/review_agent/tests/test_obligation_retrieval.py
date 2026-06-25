"""Tests for scoped obligation retrieval (Phase R4)."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk, RetrievalHit
from review_agent.config import ReviewSettings
from review_agent.graph.obligation_retrieval_nodes import obligation_retrieval_node
from review_agent.schemas.obligation import ContractObligation
from review_agent.schemas.routing_plan import CatalogMatchResult, ObligationRoutingPlan
from review_agent.services.obligation_retrieval import retrieve_for_obligation


def _hit(doc_id: str, *, chunk_id: str = "c1", score: float = 0.8) -> RetrievalHit:
    chunk = IndexedChunk(
        chunk_id=chunk_id,
        document_id=UUID(doc_id) if len(doc_id) == 36 else uuid4(),
        tenant_id="t1",
        kind=DocumentKind.POLICY,
        chunk_role=ChunkRole.PARENT,
        section_id="1",
        section_path="1",
        title="Security Practices",
        text="encryption controls",
    )
    if len(doc_id) == 36:
        chunk = chunk.model_copy(update={"document_id": UUID(doc_id)})
    return RetrievalHit(parent_chunk=chunk, matched_child_ids=[], score=score)


@pytest.mark.asyncio
async def test_obligation_retrieval_ipc_skip():
    client = AsyncMock()
    ob = ContractObligation(obligation_id="10.1-o0", section_id="10.1", text="Governed by Wyoming law.")
    plan = ObligationRoutingPlan(
        obligation_id="10.1-o0",
        routing_source="skipped_boilerplate",
        confidence=0.0,
    )
    match = CatalogMatchResult(obligation_id="10.1-o0", route_decision="ipc")
    bundle = await retrieve_for_obligation(
        client,
        obligation=ob,
        plan=plan,
        match=match,
        tenant_id="t1",
        contract_type=None,
        policy_type=None,
        settings=ReviewSettings(retrieval_relevance_gate_enabled=False),
    )
    assert bundle.skipped_reason == "ipc_preflight"
    assert bundle.policy_hits == []
    client.search_policy_recall.assert_not_called()
    client.search_policy_fts.assert_not_called()


@pytest.mark.asyncio
async def test_obligation_retrieval_respects_fence(monkeypatch):
    sp_id = str(uuid4())
    captured: list[list[UUID]] = []

    async def _mock_attempt(client, **kwargs):
        captured.append(list(kwargs.get("filter_doc_ids") or []))
        return [_hit(sp_id, score=0.9)], {"query": kwargs.get("query"), "final_count": 1}

    monkeypatch.setattr(
        "review_agent.services.obligation_retrieval.retrieve_hybrid_attempt",
        _mock_attempt,
    )
    ob = ContractObligation(
        obligation_id="2.3-o0",
        section_id="2.3",
        text="Comply with Security Practices Policy.",
    )
    plan = ObligationRoutingPlan(
        obligation_id="2.3-o0",
        search_queries=["security practices encryption"],
        confidence=0.95,
    )
    match = CatalogMatchResult(
        obligation_id="2.3-o0",
        candidate_doc_ids=[sp_id],
        route_decision="compare",
    )
    bundle = await retrieve_for_obligation(
        AsyncMock(),
        obligation=ob,
        plan=plan,
        match=match,
        tenant_id="t1",
        contract_type=None,
        policy_type=None,
        settings=ReviewSettings(
            retrieval_relevance_gate_enabled=False,
            obligation_retrieval_max_queries=1,
        ),
    )
    assert captured
    assert {str(doc_id) for doc_id in captured[0]} == {sp_id}
    assert str(bundle.policy_hits[0].parent_chunk.document_id) == sp_id


@pytest.mark.asyncio
async def test_obligation_retrieval_union_queries(monkeypatch):
    doc_a = str(uuid4())
    doc_b = str(uuid4())
    calls: list[str] = []

    async def _mock_attempt(client, *, query, filter_doc_ids, **kwargs):
        calls.append(query)
        if "breach" in query:
            return [_hit(doc_a, chunk_id="a", score=0.7)], {"final_count": 1}
        return [_hit(doc_b, chunk_id="b", score=0.9)], {"final_count": 1}

    monkeypatch.setattr(
        "review_agent.services.obligation_retrieval.retrieve_hybrid_attempt",
        _mock_attempt,
    )
    ob = ContractObligation(obligation_id="x-o0", section_id="2.3", text="incident")
    plan = ObligationRoutingPlan(
        obligation_id="x-o0",
        search_queries=["breach notification", "incident response timeline"],
        confidence=0.9,
    )
    match = CatalogMatchResult(
        obligation_id="x-o0",
        candidate_doc_ids=[doc_a, doc_b],
        route_decision="compare",
    )
    bundle = await retrieve_for_obligation(
        AsyncMock(),
        obligation=ob,
        plan=plan,
        match=match,
        tenant_id="t1",
        contract_type=None,
        policy_type=None,
        settings=ReviewSettings(
            retrieval_relevance_gate_enabled=False,
            obligation_retrieval_max_queries=2,
        ),
    )
    assert len(calls) == 2
    assert {str(hit.parent_chunk.document_id) for hit in bundle.policy_hits} == {doc_a, doc_b}


@pytest.mark.asyncio
async def test_governing_law_no_ir_chunks():
    client = AsyncMock()
    ob = ContractObligation(obligation_id="10.1-o0", section_id="10.1", text="Wyoming law.")
    plan = ObligationRoutingPlan(obligation_id="10.1-o0", routing_source="skipped_boilerplate")
    match = CatalogMatchResult(obligation_id="10.1-o0", route_decision="ipc")
    bundle = await retrieve_for_obligation(
        client,
        obligation=ob,
        plan=plan,
        match=match,
        tenant_id="t1",
        contract_type=None,
        policy_type=None,
    )
    assert bundle.skipped_reason == "ipc_preflight"
    client.search_policy_recall.assert_not_called()


@pytest.mark.asyncio
async def test_graph_node_flag_off(monkeypatch):
    monkeypatch.setattr(
        "review_agent.graph.obligation_retrieval_nodes.get_settings",
        lambda: ReviewSettings(obligation_routing_enabled=False),
    )
    out = await obligation_retrieval_node({"tenant_id": "t", "obligations": []}, AsyncMock())
    assert out == {}
