"""Tests for compare hit selection (Phase 22 P4)."""

from uuid import uuid4

from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk, RetrievalHit

from review_agent.config import ReviewSettings
from review_agent.services.compare_hit_selection import filter_hits_for_compare, select_compare_hits


def _policy_hit(text: str, *, categories: list[str] | None = None, score: float = 1.0) -> RetrievalHit:
    doc_id = uuid4()
    metadata = {"categories": categories} if categories else {}
    chunk = IndexedChunk(
        chunk_id=f"p-{doc_id}",
        document_id=doc_id,
        tenant_id="demo",
        kind=DocumentKind.POLICY,
        chunk_role=ChunkRole.PARENT,
        section_id="pol",
        section_path="pol",
        title="Policy",
        text=text,
        metadata=metadata,
    )
    return RetrievalHit(parent_chunk=chunk, score=score)


def test_select_hits_category_aligned_prefers_matching_family():
    hits = [
        _policy_hit("HR policy text", categories=["human_resources"], score=0.99),
        _policy_hit("Security policy text", categories=["security"], score=0.95),
        _policy_hit("Compliance policy text", categories=["compliance"], score=0.90),
    ]
    settings = ReviewSettings(
        compare_policy_hit_mode="category_aligned",
        compare_max_policy_hits=3,
        compare_hit_min_relevance_score=0.2,
    )
    selected = select_compare_hits(
        hits,
        section_categories=["security"],
        section_title="Security Measures",
        settings=settings,
    )
    assert len(selected) == 1
    assert "Security" in selected[0].parent_chunk.text


def test_select_hits_drops_low_relevance_aligned_hit():
    hits = [
        _policy_hit("Security policy text", categories=["security", "compliance"], score=0.95),
        _policy_hit("Weak overlap policy", categories=["compliance"], score=0.90),
    ]
    settings = ReviewSettings(
        compare_policy_hit_mode="category_aligned",
        compare_max_policy_hits=2,
        compare_hit_min_relevance_score=0.35,
    )
    selected = select_compare_hits(
        hits,
        section_categories=["security"],
        section_title="Security Measures",
        settings=settings,
    )
    assert len(selected) == 1
    assert "Security" in selected[0].parent_chunk.text


def test_select_hits_fallback_primary_when_no_overlap():
    hits = [
        _policy_hit("HR policy text", categories=["human_resources"], score=0.99),
        _policy_hit("Compliance policy text", categories=["compliance"], score=0.90),
    ]
    settings = ReviewSettings(compare_policy_hit_mode="category_aligned")
    selected = select_compare_hits(
        hits,
        section_categories=["security"],
        settings=settings,
    )
    assert len(selected) == 1
    assert selected[0] is hits[0]


def test_primary_only_mode_returns_one_hit():
    hits = [_policy_hit(f"Policy {i}", score=1.0 - i * 0.1) for i in range(5)]
    settings = ReviewSettings(compare_policy_hit_mode="primary_only")
    selected = select_compare_hits(hits, section_categories=["security"], settings=settings)
    assert len(selected) == 1
    assert selected[0] is hits[0]


def test_filter_hits_for_compare_stats():
    hits_by_section = {
        "s1": [
            _policy_hit("HR", categories=["human_resources"]),
            _policy_hit("Security", categories=["security"]),
        ],
        "s2": [
            _policy_hit("Only HR", categories=["human_resources"]),
        ],
    }
    categories = {"s1": ["security"], "s2": ["security"]}
    settings = ReviewSettings(compare_policy_hit_mode="category_aligned")
    filtered, stats = filter_hits_for_compare(
        hits_by_section,
        categories,
        settings=settings,
    )
    assert len(filtered["s1"]) == 1
    assert "Security" in filtered["s1"][0].parent_chunk.text
    assert len(filtered["s2"]) == 1
    assert stats["mode"] == "category_aligned"
    assert stats["category_aligned_sections"] == 1
    assert stats["fallback_primary_sections"] == 1
