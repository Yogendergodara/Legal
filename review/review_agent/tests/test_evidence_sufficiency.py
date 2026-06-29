"""Tests for evidence sufficiency gating (Phase R5)."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk, RetrievalHit
from review_agent.config import ReviewSettings
from review_agent.graph.obligation_retrieval_nodes import evidence_sufficiency_node
from review_agent.schemas.obligation import ContractObligation
from review_agent.schemas.obligation_retrieval import ObligationRetrievalBundle
from review_agent.schemas.evidence_sufficiency import EvidenceSufficiencyResult
from review_agent.schemas.routing_plan import CatalogMatchResult, ObligationRoutingPlan
from review_agent.services.evidence_sufficiency import (
    _hits_pass_gates,
    evaluate_evidence_sufficiency,
    tally_skip_reasons,
)


def _hit(*, score: float = 0.9, title: str = "incident notification") -> RetrievalHit:
    chunk = IndexedChunk(
        chunk_id="c1",
        document_id=uuid4(),
        tenant_id="t1",
        kind=DocumentKind.POLICY,
        chunk_role=ChunkRole.PARENT,
        section_id="1",
        section_path="1",
        title=title,
        text=f"{title} requirements timeline",
    )
    return RetrievalHit(parent_chunk=chunk, matched_child_ids=[], score=score)


def _obligation(**kwargs) -> ContractObligation:
    defaults = {"obligation_id": "2.3-o1", "section_id": "2.3", "text": "Notify within 8 hours of incident."}
    defaults.update(kwargs)
    return ContractObligation(**defaults)


def test_sufficiency_zero_hits_ipc():
    ob = _obligation()
    plan = ObligationRoutingPlan(obligation_id=ob.obligation_id, confidence=0.9)
    match = CatalogMatchResult(obligation_id=ob.obligation_id, route_decision="compare")
    bundle = ObligationRetrievalBundle(
        obligation_id=ob.obligation_id,
        section_id=ob.section_id,
        policy_hits=[],
    )
    result = evaluate_evidence_sufficiency(
        obligation=ob,
        plan=plan,
        match=match,
        bundle=bundle,
        settings=ReviewSettings(),
    )
    assert result.decision == "ipc"
    assert result.reason == "insufficient_hits"


def test_sufficiency_high_confidence_compare():
    ob = _obligation()
    plan = ObligationRoutingPlan(
        obligation_id=ob.obligation_id,
        confidence=0.9,
        concepts=["incident", "notification"],
    )
    match = CatalogMatchResult(obligation_id=ob.obligation_id, route_decision="compare")
    bundle = ObligationRetrievalBundle(
        obligation_id=ob.obligation_id,
        section_id=ob.section_id,
        policy_hits=[_hit(), _hit(score=0.85)],
    )
    result = evaluate_evidence_sufficiency(
        obligation=ob,
        plan=plan,
        match=match,
        bundle=bundle,
        settings=ReviewSettings(evidence_min_score=0.35),
    )
    assert result.decision == "compare"


def test_sufficiency_low_confidence_ipc():
    doc_id = str(uuid4())
    ob = _obligation()
    plan = ObligationRoutingPlan(obligation_id=ob.obligation_id, confidence=0.4)
    match = CatalogMatchResult(
        obligation_id=ob.obligation_id,
        route_decision="expand",
        candidate_doc_ids=[doc_id],
    )
    bundle = ObligationRetrievalBundle(
        obligation_id=ob.obligation_id,
        section_id=ob.section_id,
        policy_hits=[_hit(score=0.1)],
        candidate_doc_ids=[doc_id],
    )
    result = evaluate_evidence_sufficiency(
        obligation=ob,
        plan=plan,
        match=match,
        bundle=bundle,
        settings=ReviewSettings(evidence_expand_max_rounds=0),
    )
    assert result.decision == "ipc"
    assert result.reason == "low_routing_confidence"


def test_sufficiency_weak_hit_expand():
    ob = _obligation()
    plan = ObligationRoutingPlan(obligation_id=ob.obligation_id, confidence=0.75)
    match = CatalogMatchResult(obligation_id=ob.obligation_id, route_decision="expand")
    bundle = ObligationRetrievalBundle(
        obligation_id=ob.obligation_id,
        section_id=ob.section_id,
        policy_hits=[_hit(score=0.1)],
    )
    result = evaluate_evidence_sufficiency(
        obligation=ob,
        plan=plan,
        match=match,
        bundle=bundle,
        settings=ReviewSettings(evidence_min_score=0.35),
        expand_round=0,
    )
    assert result.decision == "expand"


def test_sufficiency_boilerplate_ipc():
    ob = ContractObligation(
        obligation_id="10.5-o0",
        section_id="10.5",
        text="All notices shall be in writing.",
        is_boilerplate=True,
    )
    plan = ObligationRoutingPlan(
        obligation_id=ob.obligation_id,
        routing_source="skipped_boilerplate",
        confidence=0.0,
    )
    match = CatalogMatchResult(obligation_id=ob.obligation_id, route_decision="ipc")
    bundle = ObligationRetrievalBundle(
        obligation_id=ob.obligation_id,
        section_id=ob.section_id,
        skipped_reason="boilerplate",
    )
    result = evaluate_evidence_sufficiency(
        obligation=ob,
        plan=plan,
        match=match,
        bundle=bundle,
    )
    assert result.decision == "ipc"
    assert result.reason == "boilerplate"


def test_ipc_route_with_candidates_and_hits_compares():
    ob = _obligation()
    doc_id = str(uuid4())
    hit = _hit(score=0.9)
    hit.parent_chunk.document_id = doc_id  # type: ignore[misc]
    plan = ObligationRoutingPlan(
        obligation_id=ob.obligation_id,
        confidence=0.95,
        concepts=["incident", "notification"],
        routing_source="llm",
    )
    match = CatalogMatchResult(
        obligation_id=ob.obligation_id,
        route_decision="ipc",
        candidate_doc_ids=[doc_id],
    )
    bundle = ObligationRetrievalBundle(
        obligation_id=ob.obligation_id,
        section_id=ob.section_id,
        policy_hits=[hit, _hit(score=0.85)],
        candidate_doc_ids=[doc_id],
    )
    result = evaluate_evidence_sufficiency(
        obligation=ob,
        plan=plan,
        match=match,
        bundle=bundle,
        settings=ReviewSettings(evidence_min_score=0.35),
    )
    assert result.decision == "compare"
    assert result.reason == "evidence_sufficient"


def test_hits_pass_gates_concept_overlap_independent_of_score():
    settings = ReviewSettings(
        evidence_min_hits=1,
        evidence_min_score=0.35,
        evidence_min_concept_overlap=0.25,
        evidence_rerank_bypass_enabled=False,
    )
    assert not _hits_pass_gates(
        hit_count=2,
        max_score=0.9,
        concept_overlap=0.1,
        doc_coverage=1.0,
        routing_confidence=0.9,
        settings=settings,
    )
    assert _hits_pass_gates(
        hit_count=2,
        max_score=0.9,
        concept_overlap=0.5,
        doc_coverage=1.0,
        routing_confidence=0.9,
        settings=settings,
    )


def test_concept_overlap_gate_blocks_weak_evidence():
    ob = ContractObligation(
        obligation_id="x-o0",
        section_id="1",
        text="Payment terms are net thirty days from invoice date.",
    )
    plan = ObligationRoutingPlan(
        obligation_id="x-o0",
        confidence=0.9,
        concepts=["payment", "invoice"],
    )
    match = CatalogMatchResult(obligation_id="x-o0", route_decision="compare")
    bundle = ObligationRetrievalBundle(
        obligation_id="x-o0",
        section_id="1",
        policy_hits=[_hit(score=0.9, title="encryption key rotation standards")],
    )
    result = evaluate_evidence_sufficiency(
        obligation=ob,
        plan=plan,
        match=match,
        bundle=bundle,
        settings=ReviewSettings(
            evidence_min_score=0.35,
            evidence_min_concept_overlap=0.25,
            evidence_rerank_bypass_enabled=False,
        ),
    )
    assert result.decision == "ipc"
    assert result.reason == "low_concept_overlap"


def test_rerank_bypass_allows_paraphrase_with_high_score():
    """PR-01 — high rerank + marginal lexical overlap reaches compare."""
    ob = ContractObligation(
        obligation_id="n-o0",
        section_id="2.3",
        text="Vendor must notify Customer within seventy-two hours of any security incident.",
    )
    plan = ObligationRoutingPlan(
        obligation_id="n-o0",
        confidence=0.8,
        concepts=["security incident", "notification timeline"],
    )
    match = CatalogMatchResult(obligation_id="n-o0", route_decision="compare")
    bundle = ObligationRetrievalBundle(
        obligation_id="n-o0",
        section_id="2.3",
        policy_hits=[
            _hit(
                score=0.88,
                title="incident response without undue delay security events",
            )
        ],
    )
    result = evaluate_evidence_sufficiency(
        obligation=ob,
        plan=plan,
        match=match,
        bundle=bundle,
        settings=ReviewSettings(
            evidence_min_score=0.35,
            evidence_min_concept_overlap=0.15,
            evidence_rerank_bypass_enabled=True,
            evidence_rerank_bypass_min_confidence=0.55,
        ),
    )
    assert result.decision == "compare"
    assert result.reason == "evidence_sufficient"


def test_low_confidence_expand_when_fenced_candidates():
    """PR-05B — low planner confidence still expands inside tenant fence."""
    doc_id = str(uuid4())
    ob = _obligation()
    plan = ObligationRoutingPlan(obligation_id=ob.obligation_id, confidence=0.5)
    match = CatalogMatchResult(
        obligation_id=ob.obligation_id,
        route_decision="expand",
        candidate_doc_ids=[doc_id],
    )
    bundle = ObligationRetrievalBundle(
        obligation_id=ob.obligation_id,
        section_id=ob.section_id,
        policy_hits=[_hit(score=0.1)],
        candidate_doc_ids=[doc_id],
    )
    result = evaluate_evidence_sufficiency(
        obligation=ob,
        plan=plan,
        match=match,
        bundle=bundle,
        settings=ReviewSettings(evidence_min_score=0.35, evidence_expand_max_rounds=2),
        expand_round=0,
    )
    assert result.decision == "expand"
    assert result.reason == "insufficient_evidence"


def test_concept_overlap_ob04_default_threshold_allows_marginal_match():
    """OB-04: overlap 0.18 passes when evidence_min_concept_overlap=0.15 (default)."""
    ob = ContractObligation(
        obligation_id="x-o0",
        section_id="1",
        text="Payment terms are net thirty days from invoice date.",
    )
    plan = ObligationRoutingPlan(
        obligation_id="x-o0",
        confidence=0.9,
        concepts=["payment", "invoice"],
    )
    match = CatalogMatchResult(obligation_id="x-o0", route_decision="compare")
    bundle = ObligationRetrievalBundle(
        obligation_id="x-o0",
        section_id="1",
        policy_hits=[_hit(score=0.9, title="invoice payment net thirty billing")],
    )
    result = evaluate_evidence_sufficiency(
        obligation=ob,
        plan=plan,
        match=match,
        bundle=bundle,
        settings=ReviewSettings(evidence_min_concept_overlap=0.15),
    )
    assert result.decision == "compare"
    assert result.reason == "evidence_sufficient"
    results = {
        "a": EvidenceSufficiencyResult(obligation_id="a", decision="ipc", reason="routing_or_skip"),
        "b": EvidenceSufficiencyResult(obligation_id="b", decision="compare", reason="evidence_sufficient"),
        "c": EvidenceSufficiencyResult(obligation_id="c", decision="ipc", reason="routing_or_skip"),
    }
    assert tally_skip_reasons(results) == {
        "routing_or_skip": 2,
        "evidence_sufficient": 1,
    }


@pytest.mark.asyncio
async def test_graph_node_flag_off(monkeypatch):
    monkeypatch.setattr(
        "review_agent.graph.obligation_retrieval_nodes.get_settings",
        lambda: ReviewSettings(obligation_routing_enabled=False),
    )
    out = await evidence_sufficiency_node({"tenant_id": "t"}, AsyncMock())
    assert out == {}


@pytest.mark.asyncio
async def test_evidence_expand_parallel_preserves_counts(monkeypatch):
    doc_id = str(uuid4())
    obligations = []
    bundles = {}
    plans = {}
    matches = {}
    for i in range(4):
        oid = f"ob-{i}"
        obligations.append(
            ContractObligation(obligation_id=oid, section_id=f"s{i}", text=f"Notify within {i} hours.")
        )
        plans[oid] = ObligationRoutingPlan(
            obligation_id=oid,
            confidence=0.75,
            concepts=["incident", "notification"],
        ).model_dump(mode="json")
        matches[oid] = CatalogMatchResult(
            obligation_id=oid,
            candidate_doc_ids=[doc_id],
            route_decision="expand",
        ).model_dump(mode="json")
        bundles[oid] = ObligationRetrievalBundle(
            obligation_id=oid,
            section_id=f"s{i}",
            policy_hits=[_hit(score=0.1)],
            candidate_doc_ids=[doc_id],
        ).model_dump(mode="json")

    expand_calls: list[str] = []

    async def _mock_retrieve(*_args, obligation, expand_mode=False, **_kwargs):
        if expand_mode:
            expand_calls.append(obligation.obligation_id)
        return ObligationRetrievalBundle(
            obligation_id=obligation.obligation_id,
            section_id=obligation.section_id,
            policy_hits=[_hit(score=0.9)],
            candidate_doc_ids=[doc_id],
        )

    monkeypatch.setattr(
        "review_agent.graph.obligation_retrieval_nodes.get_settings",
        lambda: ReviewSettings(
            obligation_routing_enabled=True,
            obligation_routing_tenant_allowlist="t1",
            evidence_sufficiency_enabled=True,
            evidence_expand_concurrency=4,
            evidence_min_score=0.35,
        ),
    )
    monkeypatch.setattr(
        "review_agent.graph.obligation_retrieval_nodes.retrieve_for_obligation",
        _mock_retrieve,
    )
    monkeypatch.setattr(
        "review_agent.graph.obligation_retrieval_nodes.obligation_routing_active",
        lambda *_args, **_kwargs: True,
    )

    out = await evidence_sufficiency_node(
        {
            "tenant_id": "t1",
            "obligations": [o.model_dump(mode="json") for o in obligations],
            "obligation_retrieval_by_id": bundles,
            "obligation_routing_by_id": plans,
            "obligation_catalog_match_by_id": matches,
            "indexed_policies": [{"document_id": doc_id}],
        },
        AsyncMock(),
    )
    stats = out.get("compliance_stats") or {}
    assert stats.get("obligation_evidence_expand_count") == 4
    assert len(expand_calls) == 4
    assert stats.get("obligation_compare_ready_count") == 4
