"""OI-1 obligation IPC recovery tests."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk, RetrievalHit
from review_agent.config import ReviewSettings
from review_agent.schemas.obligation import ContractObligation
from review_agent.schemas.obligation_retrieval import ObligationRetrievalBundle
from review_agent.schemas.routing_plan import CatalogMatchResult, ObligationRoutingPlan
from review_agent.services.catalog_matcher import match_obligation_to_catalog
from review_agent.services.catalog_registry import CatalogEntry
from review_agent.services.evidence_sufficiency import evaluate_evidence_sufficiency
from review_agent.services.obligation_relevance import obligation_relevance_categories


def _entry(document_id: str, title: str) -> CatalogEntry:
    return CatalogEntry(
        document_id=document_id,
        policy_ref=title.lower().replace(" ", "-"),
        title=title,
        aliases=[title],
        topics=[],
        summary=title,
    )


@pytest.mark.asyncio
async def test_catalog_title_fallback_fills_candidates():
    doc_id = str(uuid4())
    client = AsyncMock()
    client.search_policy_catalog.return_value = []
    plan = ObligationRoutingPlan(
        obligation_id="4-o0",
        search_queries=["generic vendor clause"],
        confidence=0.9,
        routing_source="llm",
    )
    match = await match_obligation_to_catalog(
        plan,
        client=client,
        tenant_id="tenant",
        catalog_entries=[_entry(doc_id, "Security Practices Policy")],
        allowed_doc_ids={doc_id},
        settings=ReviewSettings(catalog_match_obligation_fallback_enabled=True),
        obligation_text="Must comply with Security Practices Policy encryption requirements.",
        section_title="Security",
    )
    assert match.candidate_doc_ids == [doc_id]
    assert match.route_decision in ("expand", "compare")


def test_routing_or_skip_only_when_no_candidates():
    ob = ContractObligation(obligation_id="a-o0", section_id="1", text="Notify within 8 hours.")
    plan = ObligationRoutingPlan(
        obligation_id="a-o0",
        confidence=0.9,
        concepts=["incident", "notification"],
    )
    empty_match = CatalogMatchResult(
        obligation_id="a-o0",
        route_decision="ipc",
        candidate_doc_ids=[],
    )
    empty_bundle = ObligationRetrievalBundle(
        obligation_id="a-o0",
        section_id="1",
        skipped_reason="ipc_preflight",
    )
    skipped = evaluate_evidence_sufficiency(
        obligation=ob,
        plan=plan,
        match=empty_match,
        bundle=empty_bundle,
    )
    assert skipped.decision == "ipc"
    assert skipped.reason == "routing_or_skip"

    doc_id = str(uuid4())
    candidate_match = CatalogMatchResult(
        obligation_id="a-o0",
        route_decision="ipc",
        candidate_doc_ids=[doc_id],
    )
    chunk = IndexedChunk(
        chunk_id="c1",
        document_id=UUID(doc_id),
        tenant_id="t1",
        kind=DocumentKind.POLICY,
        chunk_role=ChunkRole.PARENT,
        section_id="1",
        section_path="1",
        title="incident notification",
        text="incident notification timeline requirements",
    )
    hit = RetrievalHit(parent_chunk=chunk, matched_child_ids=[], score=0.9)
    ready_bundle = ObligationRetrievalBundle(
        obligation_id="a-o0",
        section_id="1",
        policy_hits=[hit, hit],
        candidate_doc_ids=[doc_id],
    )
    ready = evaluate_evidence_sufficiency(
        obligation=ob,
        plan=plan,
        match=candidate_match,
        bundle=ready_bundle,
        settings=ReviewSettings(evidence_min_score=0.35),
    )
    assert ready.decision == "compare"
    assert ready.reason != "routing_or_skip"


def test_obligation_relevance_uses_lexical_taxonomy():
    section = IndexedChunk(
        chunk_id="2.3",
        document_id=uuid4(),
        tenant_id="t1",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id="2.3",
        section_path="2.3",
        title="Security Incident Reporting",
        text="Report security incidents within required timelines.",
    )
    ob = ContractObligation(
        obligation_id="2.3-o0",
        section_id="2.3",
        text="Report security incidents promptly.",
        obligation_type="incident_reporting",
    )
    plan = ObligationRoutingPlan(
        obligation_id="2.3-o0",
        concepts=["general", "security", "notification"],
    )
    cats, source = obligation_relevance_categories(plan=plan, obligation=ob, section=section)
    assert source == "taxonomy"
    assert "incident_reporting" in cats
    assert "saas" not in " ".join(cats)
