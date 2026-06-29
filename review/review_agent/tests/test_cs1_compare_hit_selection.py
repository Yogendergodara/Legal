"""CS-1 compare hit selection recovery tests."""

from __future__ import annotations

from uuid import UUID, uuid4

from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk, RetrievalHit
from review_agent.config import ReviewSettings
from review_agent.services.compare_hit_selection import filter_hits_for_compare, select_compare_hits

IR_DOC = UUID("00000000-0000-0000-0000-000000000003")


def _policy_hit(
    text: str,
    *,
    categories: list[str] | None = None,
    score: float = 0.9,
    doc_id: UUID | None = None,
) -> RetrievalHit:
    document_id = doc_id or uuid4()
    metadata = {"categories": categories} if categories else {}
    chunk = IndexedChunk(
        chunk_id=f"p-{document_id}",
        document_id=document_id,
        tenant_id="demo",
        kind=DocumentKind.POLICY,
        chunk_role=ChunkRole.PARENT,
        section_id="pol",
        section_path="pol",
        title=text[:40],
        text=text,
        metadata=metadata,
    )
    return RetrievalHit(parent_chunk=chunk, score=score)


def test_trusted_gate_fallback_uses_compatible_hits():
    hits = [
        _policy_hit(
            "General security practices overview without specific taxonomy tags",
            categories=["general"],
        )
    ]
    settings = ReviewSettings(
        compare_policy_hit_mode="category_aligned",
        compare_hit_min_relevance_score=0.35,
        compare_hit_trust_retrieval_gate=True,
        retrieval_coverage_filter_aligned=True,
    )
    selected, used_trusted = select_compare_hits(
        hits,
        section_categories=["security"],
        section_title="Supply Chain Security",
        settings=settings,
        retrieval_gate_applied=True,
    )
    assert used_trusted is True
    assert len(selected) == 1


def test_ungated_strict_still_empty():
    hits = [
        _policy_hit("HR policy text", categories=["human_resources"]),
        _policy_hit("Compliance policy text", categories=["compliance"]),
    ]
    settings = ReviewSettings(
        compare_policy_hit_mode="category_aligned",
        compare_hit_trust_retrieval_gate=True,
    )
    selected, used_trusted = select_compare_hits(
        hits,
        section_categories=["security"],
        settings=settings,
        retrieval_gate_applied=False,
    )
    assert selected == []
    assert used_trusted is False


def test_incompatible_still_empty_under_trust():
    hit = _policy_hit(
        "Incident reporting communication plan",
        categories=["incident_reporting", "records_management"],
        doc_id=IR_DOC,
    )
    settings = ReviewSettings(
        compare_policy_hit_mode="category_aligned",
        compare_hit_trust_retrieval_gate=True,
        retrieval_coverage_filter_aligned=True,
    )
    selected, used_trusted = select_compare_hits(
        [hit],
        section_categories=["governing_law"],
        section_title="Governing Law and Dispute Resolution",
        settings=settings,
        retrieval_gate_applied=True,
    )
    assert selected == []
    assert used_trusted is False


def test_filter_stats_track_trusted_and_empty():
    hits_by_section = {
        "s1": [_policy_hit("General security practices", categories=["general"])],
        "s2": [_policy_hit("Only HR", categories=["human_resources"])],
    }
    categories = {"s1": ["security"], "s2": ["security"]}
    settings = ReviewSettings(
        compare_policy_hit_mode="category_aligned",
        compare_hit_trust_retrieval_gate=True,
        retrieval_coverage_filter_aligned=True,
    )
    filtered, stats = filter_hits_for_compare(
        hits_by_section,
        categories,
        settings=settings,
        retrieval_gate_applied_by_section={"s1": True, "s2": False},
    )
    assert len(filtered["s1"]) == 1
    assert filtered["s2"] == []
    assert stats["trusted_gate_fallback_sections"] == 1
    assert stats["selection_empty_with_hits"] == 1
