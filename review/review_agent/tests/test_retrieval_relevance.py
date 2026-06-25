"""Tests for post-retrieval relevance filtering (Phase G)."""

from __future__ import annotations

from uuid import UUID

from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk, RetrievalHit
from review_agent.services.retrieval_relevance import (
    filter_hits_by_relevance,
    has_specific_category_overlap,
    is_incompatible_hit,
)


def _parent_hit(*, doc_id: UUID, title: str, categories: list[str]) -> RetrievalHit:
    chunk = IndexedChunk(
        chunk_id=f"{doc_id}:1",
        document_id=doc_id,
        tenant_id="t1",
        kind=DocumentKind.POLICY,
        chunk_role=ChunkRole.PARENT,
        section_id="1",
        section_path="1",
        title=title,
        text=f"{title} body",
        metadata={"categories": categories},
    )
    return RetrievalHit(parent_chunk=chunk, matched_child_ids=[], score=0.9)


IR_DOC = UUID("00000000-0000-0000-0000-000000000010")
SEC_DOC = UUID("00000000-0000-0000-0000-000000000011")


def test_filter_returns_empty_when_all_off_topic_without_fallback():
    ir_hit = _parent_hit(
        doc_id=IR_DOC,
        title="Communication Plan",
        categories=["incident_reporting", "records_management"],
    )
    relevant, dropped = filter_hits_by_relevance(
        [ir_hit],
        section_categories=["governing_law"],
        section_title="Governing Law and Dispute Resolution",
        min_score=0.35,
        keep_best_fallback=False,
    )
    assert relevant == []
    assert dropped == [ir_hit]


def test_filter_keep_best_fallback_legacy():
    privacy_hit = _parent_hit(
        doc_id=SEC_DOC,
        title="Privacy Policy",
        categories=["privacy"],
    )
    relevant, _ = filter_hits_by_relevance(
        [privacy_hit],
        section_categories=["governing_law"],
        section_title="Governing Law",
        min_score=0.35,
        keep_best_fallback=True,
    )
    assert len(relevant) == 1


def test_governing_law_incompatible_with_incident_response():
    ir_hit = _parent_hit(
        doc_id=IR_DOC,
        title="Incident Response Plan",
        categories=["incident_reporting"],
    )
    assert is_incompatible_hit(["governing_law"], "Governing Law", ir_hit)
    assert not has_specific_category_overlap(["governing_law"], ir_hit)


def test_notices_incompatible_with_incident_policy():
    ir_hit = _parent_hit(
        doc_id=IR_DOC,
        title="Customer Notification",
        categories=["incident_reporting"],
    )
    assert is_incompatible_hit(["general"], "Notices", ir_hit)


def test_notices_incompatible_with_incident_policy_numbered_title():
    ir_hit = _parent_hit(
        doc_id=IR_DOC,
        title="Customer Notification",
        categories=["incident_reporting"],
    )
    assert is_incompatible_hit(["general"], "10.5 Notices", ir_hit)


def test_doc_catalog_makes_chunk_incompatible_with_governing_law():
    from uuid import uuid4

    doc_id = uuid4()
    chunk = IndexedChunk(
        chunk_id=f"{doc_id}:1",
        document_id=doc_id,
        tenant_id="t1",
        kind=DocumentKind.POLICY,
        chunk_role=ChunkRole.PARENT,
        section_id="10",
        section_path="10",
        title="Communication Plan",
        text="Incident communication body",
        metadata={"categories": []},
    )
    hit = RetrievalHit(parent_chunk=chunk, matched_child_ids=[], score=0.9)
    catalog = {str(doc_id): ["incident_reporting", "records_management"]}
    assert is_incompatible_hit(
        ["governing_law"],
        "Governing Law",
        hit,
        doc_catalog_categories=catalog,
    )
    relevant, dropped = filter_hits_by_relevance(
        [hit],
        section_categories=["governing_law"],
        section_title="Governing Law",
        min_score=0.35,
        doc_catalog_categories=catalog,
        require_specific_overlap=True,
    )
    assert relevant == []
    assert dropped == [hit]


def test_security_hit_still_aligns():
    sec_hit = _parent_hit(
        doc_id=SEC_DOC,
        title="Encryption Standards",
        categories=["security", "encryption"],
    )
    assert has_specific_category_overlap(["security"], sec_hit)
    relevant, _ = filter_hits_by_relevance(
        [sec_hit],
        section_categories=["security"],
        section_title="Security Measures",
        min_score=0.35,
        keep_best_fallback=False,
    )
    assert relevant == [sec_hit]
