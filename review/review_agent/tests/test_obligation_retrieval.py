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
    assert bundle.skipped_reason == "boilerplate"
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
            obligation_retrieval_adaptive_ladder=False,
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
    assert bundle.skipped_reason == "boilerplate"
    client.search_policy_recall.assert_not_called()


@pytest.mark.asyncio
async def test_graph_node_flag_off(monkeypatch):
    monkeypatch.setattr(
        "review_agent.graph.obligation_retrieval_nodes.get_settings",
        lambda: ReviewSettings(obligation_routing_enabled=False),
    )
    out = await obligation_retrieval_node({"tenant_id": "t", "obligations": []}, AsyncMock())
    assert out == {}


def _base_settings(**kwargs) -> ReviewSettings:
    defaults = {
        "retrieval_relevance_gate_enabled": False,
        "obligation_retrieval_max_queries": 3,
        "evidence_min_hits": 1,
        "evidence_min_score": 0.35,
    }
    defaults.update(kwargs)
    return ReviewSettings(**defaults)


def _compare_match_plan():
    doc_id = str(uuid4())
    ob = ContractObligation(obligation_id="x-o0", section_id="2.3", text="incident")
    plan = ObligationRoutingPlan(
        obligation_id="x-o0",
        search_queries=["q1 breach", "q2 response", "q3 timeline"],
        confidence=0.9,
    )
    match = CatalogMatchResult(
        obligation_id="x-o0",
        candidate_doc_ids=[doc_id],
        route_decision="compare",
    )
    return doc_id, ob, plan, match


@pytest.mark.asyncio
async def test_ladder_early_exit_on_strong_first_query(monkeypatch):
    doc_id, ob, plan, match = _compare_match_plan()
    calls: list[str] = []

    async def _mock_attempt(client, *, query, **kwargs):
        calls.append(query)
        return [_hit(doc_id, score=0.9)], {"final_count": 1}

    monkeypatch.setattr(
        "review_agent.services.obligation_retrieval.retrieve_hybrid_attempt",
        _mock_attempt,
    )
    bundle = await retrieve_for_obligation(
        AsyncMock(),
        obligation=ob,
        plan=plan,
        match=match,
        tenant_id="t1",
        contract_type=None,
        policy_type=None,
        settings=_base_settings(obligation_retrieval_adaptive_ladder=True),
    )
    assert len(calls) == 1
    assert bundle.retrieval_meta.get("ladder_early_exit") is True
    assert bundle.retrieval_meta.get("queries_executed") == 1


@pytest.mark.asyncio
async def test_ladder_runs_all_queries_when_q1_empty(monkeypatch):
    doc_id, ob, plan, match = _compare_match_plan()
    calls: list[str] = []

    async def _mock_attempt(client, *, query, **kwargs):
        calls.append(query)
        if query == "q1 breach":
            return [], {"final_count": 0}
        return [_hit(doc_id, score=0.5)], {"final_count": 1}

    monkeypatch.setattr(
        "review_agent.services.obligation_retrieval.retrieve_hybrid_attempt",
        _mock_attempt,
    )
    bundle = await retrieve_for_obligation(
        AsyncMock(),
        obligation=ob,
        plan=plan,
        match=match,
        tenant_id="t1",
        contract_type=None,
        policy_type=None,
        settings=_base_settings(obligation_retrieval_adaptive_ladder=True),
    )
    assert len(calls) == 3
    assert bundle.retrieval_meta.get("ladder_early_exit") is False
    assert bundle.retrieval_meta.get("queries_executed") == 3


@pytest.mark.asyncio
async def test_ladder_runs_remaining_when_q1_weak(monkeypatch):
    doc_id, ob, plan, match = _compare_match_plan()
    calls: list[str] = []

    async def _mock_attempt(client, *, query, **kwargs):
        calls.append(query)
        if query == "q1 breach":
            return [_hit(doc_id, score=0.1)], {"final_count": 1}
        return [_hit(doc_id, score=0.8)], {"final_count": 1}

    monkeypatch.setattr(
        "review_agent.services.obligation_retrieval.retrieve_hybrid_attempt",
        _mock_attempt,
    )
    bundle = await retrieve_for_obligation(
        AsyncMock(),
        obligation=ob,
        plan=plan,
        match=match,
        tenant_id="t1",
        contract_type=None,
        policy_type=None,
        settings=_base_settings(obligation_retrieval_adaptive_ladder=True),
    )
    assert len(calls) == 3
    assert bundle.retrieval_meta.get("ladder_early_exit") is False


@pytest.mark.asyncio
async def test_parallel_queries_same_union_as_serial(monkeypatch):
    doc_a = str(uuid4())
    doc_b = str(uuid4())
    doc_c = str(uuid4())

    async def _mock_attempt(client, *, query, **kwargs):
        if "q1" in query:
            return [_hit(doc_a, chunk_id="a", score=0.7)], {"final_count": 1}
        if "q2" in query:
            return [_hit(doc_b, chunk_id="b", score=0.8)], {"final_count": 1}
        return [_hit(doc_c, chunk_id="c", score=0.9)], {"final_count": 1}

    monkeypatch.setattr(
        "review_agent.services.obligation_retrieval.retrieve_hybrid_attempt",
        _mock_attempt,
    )
    ob = ContractObligation(obligation_id="x-o0", section_id="2.3", text="incident")
    plan = ObligationRoutingPlan(
        obligation_id="x-o0",
        search_queries=["q1 breach", "q2 response", "q3 timeline"],
        confidence=0.9,
    )
    match = CatalogMatchResult(
        obligation_id="x-o0",
        candidate_doc_ids=[doc_a, doc_b, doc_c],
        route_decision="compare",
    )
    common = dict(
        obligation=ob,
        plan=plan,
        match=match,
        tenant_id="t1",
        contract_type=None,
        policy_type=None,
        settings=_base_settings(
            obligation_retrieval_adaptive_ladder=False,
            obligation_retrieval_parallel_queries=False,
        ),
    )
    serial_bundle = await retrieve_for_obligation(AsyncMock(), **common)
    parallel_bundle = await retrieve_for_obligation(
        AsyncMock(),
        **{
            **common,
            "settings": _base_settings(
                obligation_retrieval_adaptive_ladder=False,
                obligation_retrieval_parallel_queries=True,
            ),
        },
    )
    serial_ids = {h.parent_chunk.chunk_id for h in serial_bundle.policy_hits}
    parallel_ids = {h.parent_chunk.chunk_id for h in parallel_bundle.policy_hits}
    assert serial_ids == parallel_ids == {"a", "b", "c"}
    assert parallel_bundle.retrieval_meta.get("parallel_query_batch") is True


@pytest.mark.asyncio
async def test_section_hit_reuse_skips_mcp(monkeypatch):
    from review_agent.schemas.section_retrieval import SectionRetrievalBundle

    doc_id = str(uuid4())
    calls = 0

    async def _mock_attempt(client, **kwargs):
        nonlocal calls
        calls += 1
        return [_hit(doc_id, score=0.9)], {"final_count": 1}

    monkeypatch.setattr(
        "review_agent.services.obligation_retrieval.retrieve_hybrid_attempt",
        _mock_attempt,
    )
    ob = ContractObligation(obligation_id="x-o0", section_id="2.3", text="incident")
    plan = ObligationRoutingPlan(obligation_id="x-o0", search_queries=["q1"], confidence=0.9)
    match = CatalogMatchResult(
        obligation_id="x-o0",
        candidate_doc_ids=[doc_id],
        route_decision="compare",
    )
    section_bundle = SectionRetrievalBundle(
        section_id="2.3",
        categories=["incident"],
        policy_hits=[_hit(doc_id, score=0.85)],
    )
    bundle = await retrieve_for_obligation(
        AsyncMock(),
        obligation=ob,
        plan=plan,
        match=match,
        tenant_id="t1",
        contract_type=None,
        policy_type=None,
        settings=_base_settings(obligation_retrieval_section_hit_reuse=True),
        section_bundle=section_bundle,
    )
    assert calls == 0
    assert bundle.retrieval_meta.get("section_hit_reuse") is True
    assert bundle.policy_hits


@pytest.mark.asyncio
async def test_section_hit_reuse_rejected_not_remerged(monkeypatch):
    from review_agent.schemas.section_retrieval import SectionRetrievalBundle

    doc_id = str(uuid4())
    seed_chunk_id = "seed-chunk"
    mcp_chunk_id = "mcp-chunk"
    calls = 0

    async def _mock_attempt(client, **kwargs):
        nonlocal calls
        calls += 1
        return [_hit(doc_id, chunk_id=mcp_chunk_id, score=0.9)], {"final_count": 1}

    monkeypatch.setattr(
        "review_agent.services.obligation_retrieval.retrieve_hybrid_attempt",
        _mock_attempt,
    )

    sufficient_calls = 0

    def _mock_sufficient(hits, cfg):
        nonlocal sufficient_calls
        sufficient_calls += 1
        if sufficient_calls == 1:
            return True
        if sufficient_calls == 2:
            return False
        return True

    monkeypatch.setattr(
        "review_agent.services.obligation_retrieval._retrieval_hits_sufficient",
        _mock_sufficient,
    )

    ob = ContractObligation(obligation_id="x-o0", section_id="2.3", text="incident")
    plan = ObligationRoutingPlan(obligation_id="x-o0", search_queries=["q1"], confidence=0.9)
    match = CatalogMatchResult(
        obligation_id="x-o0",
        candidate_doc_ids=[doc_id],
        route_decision="compare",
    )
    section_bundle = SectionRetrievalBundle(
        section_id="2.3",
        categories=["incident"],
        policy_hits=[_hit(doc_id, chunk_id=seed_chunk_id, score=0.85)],
    )
    bundle = await retrieve_for_obligation(
        AsyncMock(),
        obligation=ob,
        plan=plan,
        match=match,
        tenant_id="t1",
        contract_type=None,
        policy_type=None,
        settings=_base_settings(obligation_retrieval_section_hit_reuse=True),
        section_bundle=section_bundle,
    )
    assert calls == 1
    assert bundle.retrieval_meta.get("section_hit_reuse_rejected") is True
    hit_ids = {h.parent_chunk.chunk_id for h in bundle.policy_hits}
    assert seed_chunk_id not in hit_ids
    assert mcp_chunk_id in hit_ids


@pytest.mark.asyncio
async def test_skip_resolved_section_obligation():
    from review_agent.schemas.section_retrieval import SectionRetrievalBundle
    from review_agent.services.obligation_retrieval import should_skip_obligation_for_resolved_section

    doc_id = str(uuid4())
    plan = ObligationRoutingPlan(obligation_id="x-o0", confidence=0.7)
    match = CatalogMatchResult(obligation_id="x-o0", route_decision="expand", confidence=0.7)
    section_bundle = SectionRetrievalBundle(
        section_id="2.3",
        categories=["incident"],
        policy_hits=[_hit(doc_id, score=0.9), _hit(doc_id, chunk_id="b", score=0.8)],
        retrieval_meta={"substantive": True},
    )
    assert should_skip_obligation_for_resolved_section(
        tenant_id="xecurify-demo",
        plan=plan,
        match=match,
        section_bundle=section_bundle,
        settings=_base_settings(
            obligation_retrieval_skip_resolved_sections=True,
            review_pipeline_mode="serial",
        ),
    )
    high_conf_match = CatalogMatchResult(
        obligation_id="x-o0",
        route_decision="compare",
        confidence=0.95,
    )
    assert not should_skip_obligation_for_resolved_section(
        tenant_id="xecurify-demo",
        plan=plan,
        match=high_conf_match,
        section_bundle=section_bundle,
        settings=_base_settings(obligation_retrieval_skip_resolved_sections=True),
    )


def test_skip_resolved_disabled_in_parallel_hybrid():
    from review_agent.schemas.section_retrieval import SectionRetrievalBundle
    from review_agent.services.obligation_retrieval import should_skip_obligation_for_resolved_section

    doc_id = str(uuid4())
    plan = ObligationRoutingPlan(obligation_id="x-o0", confidence=0.7)
    match = CatalogMatchResult(obligation_id="x-o0", route_decision="expand", confidence=0.7)
    section_bundle = SectionRetrievalBundle(
        section_id="2.3",
        categories=["incident"],
        policy_hits=[_hit(doc_id, score=0.9)],
        retrieval_meta={"substantive": True},
    )
    settings = _base_settings(
        obligation_retrieval_skip_resolved_sections=True,
        review_pipeline_mode="parallel_hybrid",
        obligation_skip_resolved_parallel_guard=True,
    )
    assert not should_skip_obligation_for_resolved_section(
        tenant_id="atlassian-demo",
        plan=plan,
        match=match,
        section_bundle=section_bundle,
        settings=settings,
    )
