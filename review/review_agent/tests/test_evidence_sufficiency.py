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
from review_agent.schemas.routing_plan import CatalogMatchResult, ObligationRoutingPlan
from review_agent.services.evidence_sufficiency import evaluate_evidence_sufficiency


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
    ob = _obligation()
    plan = ObligationRoutingPlan(obligation_id=ob.obligation_id, confidence=0.4)
    match = CatalogMatchResult(obligation_id=ob.obligation_id, route_decision="compare")
    bundle = ObligationRetrievalBundle(
        obligation_id=ob.obligation_id,
        section_id=ob.section_id,
        policy_hits=[_hit()],
    )
    result = evaluate_evidence_sufficiency(
        obligation=ob,
        plan=plan,
        match=match,
        bundle=bundle,
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
        skipped_reason="ipc_preflight",
    )
    result = evaluate_evidence_sufficiency(
        obligation=ob,
        plan=plan,
        match=match,
        bundle=bundle,
    )
    assert result.decision == "ipc"
    assert result.reason == "routing_or_skip"


@pytest.mark.asyncio
async def test_graph_node_flag_off(monkeypatch):
    monkeypatch.setattr(
        "review_agent.graph.obligation_retrieval_nodes.get_settings",
        lambda: ReviewSettings(obligation_routing_enabled=False),
    )
    out = await evidence_sufficiency_node({"tenant_id": "t"}, AsyncMock())
    assert out == {}
