"""Tests for retrieval relevance and policy coverage gate."""

from __future__ import annotations

from uuid import UUID, uuid4

from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk, RetrievalHit
from review_agent.config import ReviewSettings
from review_agent.services.policy_coverage import (
    apply_coverage_gate,
    filter_doc_ids_by_category_overlap,
    validate_section_coverage,
)
from review_agent.services.retrieval_relevance import filter_hits_by_relevance


def _parent_hit(*, doc_id: UUID, title: str, categories: list[str], section_id: str = "1") -> RetrievalHit:
    chunk = IndexedChunk(
        chunk_id=f"{doc_id}:{section_id}",
        document_id=doc_id,
        tenant_id="t1",
        kind=DocumentKind.POLICY,
        chunk_role=ChunkRole.PARENT,
        section_id=section_id,
        section_path=section_id,
        title=title,
        text=f"{title} body text",
        metadata={"categories": categories},
    )
    return RetrievalHit(parent_chunk=chunk, matched_child_ids=[], score=0.9)


SEC_DOC = UUID("00000000-0000-0000-0000-000000000001")
RET_DOC = UUID("00000000-0000-0000-0000-000000000002")


def test_filter_drops_off_topic_data_retention_for_security_section():
    security_hit = _parent_hit(
        doc_id=SEC_DOC,
        title="Encryption Standards",
        categories=["security"],
    )
    retention_hit = _parent_hit(
        doc_id=RET_DOC,
        title="Secure Deletion",
        categories=["data_retention", "privacy"],
    )
    relevant, dropped = filter_hits_by_relevance(
        [retention_hit, security_hit],
        section_categories=["security"],
        section_title="Security Measures",
    )
    assert any(h.parent_chunk.document_id == SEC_DOC for h in relevant)
    assert any(h.parent_chunk.document_id == RET_DOC for h in dropped)


IR_DOC = UUID("00000000-0000-0000-0000-000000000003")


def test_coverage_gate_insufficient_when_only_off_topic_hits():
    section = IndexedChunk(
        chunk_id="c1",
        document_id=uuid4(),
        tenant_id="t1",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id="5.2",
        section_path="5.2",
        title="Human Rights & Labor",
        text="Support human rights.",
    )
    hit = _parent_hit(
        doc_id=RET_DOC,
        title="Data Retention Schedule",
        categories=["data_retention", "compliance"],
    )
    settings = ReviewSettings(
        policy_coverage_enabled=True,
        policy_coverage_min_score=0.34,
        retrieval_relevance_min_score=0.2,
        compare_hit_min_relevance_score=0.35,
        policy_coverage_require_specific_overlap=True,
    )
    result = validate_section_coverage(
        section,
        [hit],
        section_categories=["human_rights", "employment"],
        settings=settings,
    )
    assert result.insufficient

    filtered, ipc, warnings = apply_coverage_gate(
        [section],
        {"5.2": [hit]},
        {"5.2": ["human_rights", "employment"]},
        settings=settings,
    )
    assert ipc
    assert filtered["5.2"] == []
    assert warnings


def test_governing_law_vs_incident_response_insufficient():
    section = IndexedChunk(
        chunk_id="c10",
        document_id=uuid4(),
        tenant_id="t1",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id="10.1",
        section_path="10.1",
        title="Governing Law and Dispute Resolution",
        text="Wyoming law applies.",
    )
    hit = _parent_hit(
        doc_id=IR_DOC,
        title="Communication Plan",
        categories=["incident_reporting", "records_management"],
    )
    result = validate_section_coverage(
        section,
        [hit],
        section_categories=["governing_law"],
        settings=ReviewSettings(
            compare_hit_min_relevance_score=0.35,
            policy_coverage_require_specific_overlap=True,
        ),
    )
    assert result.insufficient
    assert result.reason == "incompatible_policy_family"


def test_notices_vs_incident_insufficient():
    section = IndexedChunk(
        chunk_id="c105",
        document_id=uuid4(),
        tenant_id="t1",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id="10.5",
        section_path="10.5",
        title="Notices",
        text="All notices in writing.",
    )
    hit = _parent_hit(
        doc_id=IR_DOC,
        title="Customer Notification",
        categories=["incident_reporting"],
    )
    result = validate_section_coverage(
        section,
        [hit],
        section_categories=["general"],
        settings=ReviewSettings(compare_hit_min_relevance_score=0.35),
    )
    assert result.insufficient
    assert result.reason == "notice_vs_incident_mismatch"


def test_governing_law_multi_hit_ir_and_privacy_insufficient():
    from uuid import uuid4

    privacy_doc = UUID("00000000-0000-0000-0000-000000000020")
    section = IndexedChunk(
        chunk_id="c10",
        document_id=uuid4(),
        tenant_id="t1",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id="10.1",
        section_path="10.1",
        title="Governing Law",
        text="Wyoming law applies.",
    )
    ir_hit = _parent_hit(
        doc_id=IR_DOC,
        title="Communication Plan",
        categories=["incident_reporting"],
    )
    privacy_hit = _parent_hit(
        doc_id=privacy_doc,
        title="Privacy Notice",
        categories=["privacy", "data_subject_rights"],
    )
    result = validate_section_coverage(
        section,
        [ir_hit, privacy_hit],
        section_categories=["governing_law"],
        settings=ReviewSettings(
            compare_hit_min_relevance_score=0.35,
            policy_coverage_require_specific_overlap=True,
        ),
    )
    assert result.insufficient


def test_overlap_filter_returns_empty_when_no_match():
    doc_a = UUID("00000000-0000-0000-0000-0000000000aa")
    doc_b = UUID("00000000-0000-0000-0000-0000000000bb")
    kept = filter_doc_ids_by_category_overlap(
        [doc_a, doc_b],
        section_categories=["liability"],
        catalog_categories={
            str(doc_a): ["privacy", "compliance"],
            str(doc_b): ["data_retention"],
        },
        min_overlap=1,
    )
    assert kept == []
