"""Tests for obligation relevance taxonomy categories (Phase OR-1)."""

from __future__ import annotations

from uuid import UUID, uuid4

from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk, RetrievalHit
from review_agent.config import ReviewSettings
from review_agent.schemas.obligation import ContractObligation
from review_agent.schemas.routing_plan import ObligationRoutingPlan
from review_agent.services.obligation_relevance import obligation_relevance_categories
from review_agent.services.retrieval_relevance import filter_hits_by_relevance


def _section(*, section_id: str, title: str, text: str) -> IndexedChunk:
    return IndexedChunk(
        chunk_id=section_id,
        document_id=uuid4(),
        tenant_id="t1",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id=section_id,
        section_path=section_id,
        title=title,
        text=text,
    )


def _policy_hit(*, categories: list[str], score: float = 0.9) -> RetrievalHit:
    doc_id = uuid4()
    chunk = IndexedChunk(
        chunk_id=f"p-{doc_id}",
        document_id=doc_id,
        tenant_id="t1",
        kind=DocumentKind.POLICY,
        chunk_role=ChunkRole.PARENT,
        section_id="1",
        section_path="1",
        title="Payment terms",
        text="payment and invoicing terms for subscriptions",
        metadata={"categories": categories},
    )
    return RetrievalHit(parent_chunk=chunk, matched_child_ids=[], score=score)


def test_lexical_payment_section_beats_free_text_concepts():
    section = _section(
        section_id="10.1",
        title="10.1. Fees",
        text="Payment terms and invoice schedule for SaaS subscriptions.",
    )
    ob = ContractObligation(
        obligation_id="10.1-o1",
        section_id="10.1",
        text="Automatic renewal based on prior subscription term length.",
    )
    plan = ObligationRoutingPlan(
        obligation_id="10.1-o1",
        concepts=["SaaS subscription payment terms", "automatic renewal"],
    )
    cats, source = obligation_relevance_categories(plan=plan, obligation=ob, section=section)
    assert source == "taxonomy"
    assert "payment" in cats


def test_obligation_type_merged_and_normalized():
    section = _section(section_id="3", title="Retention", text="Data handling.")
    ob = ContractObligation(
        obligation_id="3-o0",
        section_id="3",
        text="Delete data after retention period.",
        obligation_type="data_retention",
    )
    plan = ObligationRoutingPlan(obligation_id="3-o0", concepts=["vendor data"])
    cats, source = obligation_relevance_categories(plan=plan, obligation=ob, section=section)
    assert source == "taxonomy"
    assert "data_retention" in cats


def test_no_section_falls_back_general():
    ob = ContractObligation(obligation_id="x-o0", section_id="9", text="Misc clause.")
    plan = ObligationRoutingPlan(obligation_id="x-o0", concepts=["misc topic phrase"])
    cats, source = obligation_relevance_categories(plan=plan, obligation=ob, section=None)
    assert source == "general_fallback"
    assert cats == ["general"]


def test_filter_keeps_payment_hit_with_taxonomy_cats():
    section = _section(
        section_id="10.1",
        title="10.1. Fees",
        text="Payment and invoice terms for subscriptions.",
    )
    ob = ContractObligation(
        obligation_id="10.1-o1",
        section_id="10.1",
        text="SaaS subscription renewal payment.",
    )
    plan = ObligationRoutingPlan(
        obligation_id="10.1-o1",
        concepts=["SaaS subscription payment terms"],
    )
    categories, _ = obligation_relevance_categories(plan=plan, obligation=ob, section=section)
    hit = _policy_hit(categories=["payment", "sla"], score=0.25)
    relevant, dropped = filter_hits_by_relevance(
        [hit],
        section_categories=categories,
        section_title=section.title,
        min_score=0.2,
        keep_best_fallback=True,
        require_specific_overlap=True,
        fallback_on_overlap_miss=True,
    )
    assert relevant == [hit]
    assert dropped == []

    free_text_cats = ["saas_subscription_payment_terms"]
    relevant_free, _ = filter_hits_by_relevance(
        [hit],
        section_categories=free_text_cats,
        section_title=section.title,
        min_score=0.2,
        keep_best_fallback=False,
        require_specific_overlap=True,
        fallback_on_overlap_miss=False,
    )
    assert relevant_free == []


def test_legacy_concept_mode_when_flag_off():
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
    cats, source = obligation_relevance_categories(
        plan=plan,
        obligation=ob,
        section=None,
        settings=ReviewSettings(obligation_relevance_use_lexical_categories=False),
    )
    assert source == "concepts"
    assert "incident_reporting" in cats
    assert "security" in cats
