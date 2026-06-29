"""Tests for retrieval/coverage double-filter alignment (Phase DF-1)."""

from __future__ import annotations

from uuid import UUID, uuid4

from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk, RetrievalHit
from review_agent.config import ReviewSettings
from review_agent.services.policy_coverage import apply_coverage_gate, validate_section_coverage
from review_agent.services.retrieval_relevance import filter_hits_by_relevance, relevance_filter_kwargs

SEC_DOC = UUID("00000000-0000-0000-0000-000000000001")
RET_DOC = UUID("00000000-0000-0000-0000-000000000002")
IR_DOC = UUID("00000000-0000-0000-0000-000000000003")
COMPLIANCE_DOC = UUID("00000000-0000-0000-0000-000000000010")


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
        text=f"{title} body text for substring tests here.",
        metadata={"categories": categories},
    )
    return RetrievalHit(parent_chunk=chunk, matched_child_ids=[], score=0.9)


def test_retrieval_and_coverage_same_overlap_flag():
    cfg = ReviewSettings(policy_coverage_require_specific_overlap=True)
    assert relevance_filter_kwargs(cfg, stage="retrieval")["require_specific_overlap"] == relevance_filter_kwargs(
        cfg, stage="coverage"
    )["require_specific_overlap"]


def test_cisco_like_compliance_hit_passes_narrow_coverage():
    section = IndexedChunk(
        chunk_id="c1",
        document_id=uuid4(),
        tenant_id="t1",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id="1",
        section_path="1",
        title="Supplier Code of Conduct",
        text="Supplier shall comply with code of conduct requirements.",
    )
    hit = _parent_hit(
        doc_id=COMPLIANCE_DOC,
        title="Supplier Code of Conduct",
        categories=["compliance", "human_rights"],
    )
    cfg = ReviewSettings(
        retrieval_coverage_filter_aligned=True,
        policy_coverage_require_specific_overlap=True,
        compare_hit_min_relevance_score=0.35,
    )
    kw = relevance_filter_kwargs(cfg, stage="retrieval")
    relevant, _ = filter_hits_by_relevance(
        [hit],
        section_categories=["compliance"],
        section_title=section.title,
        **kw,
    )
    assert relevant
    result = validate_section_coverage(
        section,
        relevant,
        section_categories=["compliance"],
        settings=cfg,
        retrieval_gate_applied=True,
    )
    assert not result.insufficient
    assert result.relevant_hits


def test_title_only_hit_dropped_at_retrieval_with_aligned_overlap():
    general_hit = _parent_hit(
        doc_id=SEC_DOC,
        title="General Security Practices",
        categories=["general"],
    )
    cfg = ReviewSettings(
        policy_coverage_require_specific_overlap=True,
        retrieval_relevance_min_score=0.2,
        compare_hit_min_relevance_score=0.35,
    )
    kw = relevance_filter_kwargs(cfg, stage="retrieval")
    relevant, dropped = filter_hits_by_relevance(
        [general_hit],
        section_categories=["security"],
        section_title="Supply Chain Security",
        **kw,
    )
    assert not relevant
    assert dropped == [general_hit]


def test_governing_law_ir_narrow_coverage_still_ipc():
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
    cfg = ReviewSettings(retrieval_coverage_filter_aligned=True)
    result = validate_section_coverage(
        section,
        [hit],
        section_categories=["governing_law"],
        settings=cfg,
        retrieval_gate_applied=True,
    )
    assert result.insufficient
    assert result.reason == "incompatible_policy_family"


def test_notices_vs_incident_narrow_coverage_still_ipc():
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
    cfg = ReviewSettings(retrieval_coverage_filter_aligned=True)
    result = validate_section_coverage(
        section,
        [hit],
        section_categories=["general"],
        settings=cfg,
        retrieval_gate_applied=True,
    )
    assert result.insufficient
    assert result.reason == "notice_vs_incident_mismatch"


def test_mixed_policy_ratio_still_blocks_full_filter():
    section = IndexedChunk(
        chunk_id="s1",
        document_id=uuid4(),
        tenant_id="t1",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id="5",
        section_path="5",
        title="Security Measures",
        text="Security requirements.",
    )
    sec_hit = _parent_hit(doc_id=SEC_DOC, title="Encryption Standards", categories=["security"])
    noise_hits = [
        _parent_hit(doc_id=UUID(f"00000000-0000-0000-0000-0000000000{i:02x}"), title=f"Policy {i}", categories=["privacy"])
        for i in range(1, 4)
    ]
    hits = [sec_hit, *noise_hits]
    cfg = ReviewSettings(
        policy_coverage_min_score=0.34,
        retrieval_coverage_filter_aligned=True,
        compare_hit_min_relevance_score=0.35,
        policy_coverage_require_specific_overlap=True,
    )
    result = validate_section_coverage(
        section,
        hits,
        section_categories=["security"],
        settings=cfg,
        retrieval_gate_applied=False,
    )
    assert result.insufficient
    assert result.reason == "low_coverage_mixed_policies"


def test_apply_coverage_gate_preserves_hits_when_gate_applied():
    section = IndexedChunk(
        chunk_id="c1",
        document_id=uuid4(),
        tenant_id="t1",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id="1",
        section_path="1",
        title="Supplier Code of Conduct",
        text="Code of conduct.",
    )
    hit = _parent_hit(
        doc_id=COMPLIANCE_DOC,
        title="Code of Conduct",
        categories=["compliance"],
    )
    cfg = ReviewSettings(retrieval_coverage_filter_aligned=True)
    filtered, ipc, _warnings = apply_coverage_gate(
        [section],
        {"1": [hit]},
        {"1": ["compliance"]},
        settings=cfg,
        retrieval_gate_applied_by_section={"1": True},
    )
    assert not ipc
    assert filtered["1"] == [hit]


def test_meaning_first_coverage_allows_compatible_top_hit_without_tag_overlap():
    section = IndexedChunk(
        chunk_id="c1",
        document_id=uuid4(),
        tenant_id="t1",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id="15",
        section_path="15",
        title="Indemnification",
        text="Customer shall indemnify Atlassian.",
    )
    hit = _parent_hit(
        doc_id=uuid4(),
        title="Liability Cap",
        categories=["privacy"],
    )
    cfg = ReviewSettings(
        retrieval_meaning_first_enabled=True,
        compare_hit_allow_primary_fallback=True,
        policy_coverage_require_specific_overlap=True,
    )
    result = validate_section_coverage(
        section,
        [hit],
        section_categories=["indemnity"],
        settings=cfg,
    )
    assert not result.insufficient
    assert result.relevant_hits == [hit]
