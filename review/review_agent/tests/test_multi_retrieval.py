"""Tests for multi-path policy retrieval union and retry ladder."""

from uuid import uuid4

import pytest
from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk, RetrievalHit

from review_agent.config import ReviewSettings
from review_agent.schemas.section_classify import SectionCategoryResult
from review_agent.services.multi_retrieval import (
    _diverse_top_k,
    _normalize_path_scores,
    _query_for_attempt,
    _union_hits,
    multi_retrieve_for_section,
)


def _hit(text: str, chunk_id: str, score: float, *, doc_id=None) -> RetrievalHit:
    document_id = doc_id or uuid4()
    chunk = IndexedChunk(
        chunk_id=chunk_id,
        document_id=document_id,
        tenant_id="demo",
        kind=DocumentKind.POLICY,
        chunk_role=ChunkRole.PARENT,
        section_id=chunk_id,
        section_path=chunk_id,
        title=chunk_id,
        text=text,
    )
    return RetrievalHit(parent_chunk=chunk, score=score)


def _section() -> IndexedChunk:
    return IndexedChunk(
        chunk_id="c1",
        document_id=uuid4(),
        tenant_id="demo",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id="12.2",
        section_path="12.2",
        title="Limitation of Liability",
        text="Total liability shall not exceed twelve months fees.",
    )


def _classification(section: IndexedChunk) -> SectionCategoryResult:
    return SectionCategoryResult(
        section_id=section.section_id,
        categories=["liability"],
        query_terms=["limitation of liability", "Limitation of Liability"],
    )


def test_union_dedupes_by_parent_and_keeps_best_score():
    paths: dict[str, int] = {}
    dense = [_hit("alpha policy", "p1", 0.9)]
    fts = [_hit("alpha policy", "p1", 0.4), _hit("beta keyword match", "p2", 0.6)]
    union = _union_hits(dense, fts, paths=paths)
    assert len(union) == 2
    assert paths["union_count"] == 2
    by_id = {h.parent_chunk.chunk_id: h for h in union}
    assert by_id["p1"].score == 0.9


def test_query_for_attempt_broadens_on_retry():
    section = _section()
    classification = _classification(section)
    q0, _, hard0 = _query_for_attempt(classification, section, 0)
    q1, _, hard1 = _query_for_attempt(classification, section, 1)
    q2, cats2, hard2 = _query_for_attempt(classification, section, 2)
    assert q0 == "limitation of liability"
    assert q1 == "Limitation of Liability"
    assert hard0 and hard1
    assert not hard2
    assert "general" in cats2


def test_query_for_attempt_meaning_first_uses_section_body():
    section = _section()
    classification = _classification(section)
    settings = ReviewSettings(retrieval_meaning_first_enabled=True)
    q0, _, _ = _query_for_attempt(classification, section, 0, settings=settings)
    assert "Limitation of Liability" in q0
    assert "Total liability" in q0
    q1, _, _ = _query_for_attempt(classification, section, 1, settings=settings)
    assert q1 == "Limitation of Liability"


@pytest.mark.asyncio
async def test_multi_retrieve_merges_three_paths():
    section = _section()
    dense_hit = _hit("dense only", "d1", 0.5)
    fts_hit = _hit("twelve months fees cap", "f1", 0.7)
    meta_hit = _hit("liability policy section", "m1", 0.6)

    class FakeClient:
        async def list_policy_ids_by_categories(self, *_args, **_kwargs):
            return [uuid4()]

        async def search_policy_recall(self, _req):
            return [dense_hit]

        async def search_policy_fts(self, _req):
            return [fts_hit]

        async def search_policy_by_categories(self, _req, *, categories):
            assert categories
            return [meta_hit]

    bundle = await multi_retrieve_for_section(
        FakeClient(),
        tenant_id="demo",
        section=section,
        contract_type="msa",
        policy_type=None,
        classification=_classification(section),
    )
    assert bundle.section_id == "12.2"
    assert len(bundle.policy_hits) <= 10
    assert bundle.retrieval_meta.get("dense_count") == 1
    assert bundle.retrieval_meta.get("fts_count") == 1
    assert bundle.retrieval_meta.get("metadata_count") == 1
    assert len(bundle.retrieval_meta.get("attempts") or []) == 1
    ids = {h.parent_chunk.chunk_id for h in bundle.policy_hits}
    assert {"d1", "f1", "m1"}.issubset(ids)


@pytest.mark.asyncio
async def test_multi_retrieve_retries_when_first_attempt_empty():
    section = _section()
    hit = _hit("found on retry", "r1", 0.8)
    recall_calls: list[str] = []

    class FakeClient:
        async def list_policy_ids_by_categories(self, *_args, **_kwargs):
            return [uuid4()]

        async def search_policy_recall(self, req):
            recall_calls.append(req.query)
            if req.query == "Limitation of Liability":
                return [hit]
            return []

        async def search_policy_fts(self, _req):
            return []

        async def search_policy_by_categories(self, _req, *, categories):
            return []

    settings = ReviewSettings(retrieval_max_attempts=3)
    bundle = await multi_retrieve_for_section(
        FakeClient(),
        tenant_id="demo",
        section=section,
        contract_type="msa",
        policy_type=None,
        settings=settings,
        classification=_classification(section),
    )
    assert len(bundle.policy_hits) == 1
    assert len(bundle.retrieval_meta["attempts"]) == 2
    assert bundle.retrieval_meta["final_attempt"] == 1
    assert recall_calls[0] == "limitation of liability"
    assert recall_calls[1] == "Limitation of Liability"


@pytest.mark.asyncio
async def test_multi_retrieve_passes_document_ids_when_category_filter_set():
    section = _section()
    scope_id = str(uuid4())
    category_id = uuid4()
    seen_document_ids: list[list] = []

    class FakeClient:
        async def list_policy_ids_by_categories(self, *_args, **_kwargs):
            return [category_id]

        async def search_policy_recall(self, req):
            if req.document_ids is not None:
                seen_document_ids.append(list(req.document_ids))
            return []

        async def search_policy_fts(self, req):
            if req.document_ids is not None:
                seen_document_ids.append(list(req.document_ids))
            return []

        async def search_policy_by_categories(self, _req, *, categories):
            return []

    settings = ReviewSettings(
        retrieval_max_attempts=1,
        retrieval_category_hard_filter=True,
        retrieval_category_filter_fallback=False,
    )
    await multi_retrieve_for_section(
        FakeClient(),
        tenant_id="demo",
        section=section,
        contract_type="msa",
        policy_type=None,
        settings=settings,
        classification=_classification(section),
        scope_document_ids=[scope_id, str(category_id)],
    )
    assert seen_document_ids
    assert str(category_id) in {str(doc_id) for doc_id in seen_document_ids[0]}


@pytest.mark.asyncio
async def test_retrieval_general_skips_hard_filter():
    section = _section()
    hit = _hit("scoped policy hit", "g1", 0.75)
    category_filter_calls: list[list[str]] = []

    class FakeClient:
        async def list_policy_ids_by_categories(self, _tenant, categories, **_kwargs):
            category_filter_calls.append(list(categories))
            return []

        async def search_policy_recall(self, _req):
            return [hit]

        async def search_policy_fts(self, _req):
            return []

        async def search_policy_by_categories(self, _req, *, categories):
            return []

    scope_id = str(uuid4())
    classification = SectionCategoryResult(
        section_id=section.section_id,
        categories=["general"],
        query_terms=["Limitation of Liability"],
    )
    settings = ReviewSettings(
        retrieval_max_attempts=1,
        retrieval_category_hard_filter=True,
        retrieval_skip_hard_filter_for_general=True,
    )
    bundle = await multi_retrieve_for_section(
        FakeClient(),
        tenant_id="demo",
        section=section,
        contract_type="msa",
        policy_type=None,
        settings=settings,
        classification=classification,
        scope_document_ids=[scope_id],
    )
    assert category_filter_calls == []
    assert len(bundle.policy_hits) == 1
    assert bundle.retrieval_meta["attempts"][0]["category_hard_filter"] is False


def test_normalize_path_scores_fts_wins_union():
    """T1: After normalization, a dominant FTS hit should beat uniformly weak dense hits."""
    # Dense path: two hits close together at ~0.3 (uniformly weak)
    dense = [_hit("alpha", "d1", 0.30), _hit("beta", "d2", 0.32)]
    # FTS path: one strong hit and one weak — the strong one should normalize to 1.0
    fts = [_hit("gamma fts winner", "f1", 0.55), _hit("delta", "f2", 0.10)]

    # Without normalization, dense d2 (0.32) < fts f1 (0.55) but dense d1 (0.30) > fts f2 (0.10).
    # After normalization:
    #   dense: d1→0.0, d2→1.0 | fts: f2→0.0, f1→1.0
    # Union should keep all 4 (different chunk_ids), ranked by normalized score.
    paths: dict[str, int] = {}
    normed_dense = _normalize_path_scores(dense)
    normed_fts = _normalize_path_scores(fts)
    union = _union_hits(normed_dense, normed_fts, paths=paths)

    assert paths["union_count"] == 4
    # Both d2 and f1 should be at 1.0 (top), beating d1 and f2 at 0.0
    top_ids = {h.parent_chunk.chunk_id for h in union[:2]}
    assert "d2" in top_ids  # dense best → normalized to 1.0
    assert "f1" in top_ids  # FTS best → normalized to 1.0


def test_normalize_single_hit_unchanged():
    """Single-hit path returns unchanged (no normalization range)."""
    original = _hit("solo", "s1", 0.42)
    result = _normalize_path_scores([original])
    assert len(result) == 1
    assert result[0].score == 0.42


def test_diverse_top_k_caps_per_document():
    """T2: 5 sections from same document → at most 3 enter output."""
    shared_doc = uuid4()
    other_doc = uuid4()
    hits = [
        _hit("sec1", "p1", 0.95, doc_id=shared_doc),
        _hit("sec2", "p2", 0.90, doc_id=shared_doc),
        _hit("sec3", "p3", 0.85, doc_id=shared_doc),
        _hit("sec4", "p4", 0.80, doc_id=shared_doc),
        _hit("sec5", "p5", 0.75, doc_id=shared_doc),
        _hit("other", "o1", 0.50, doc_id=other_doc),
    ]
    result = _diverse_top_k(hits, top_k=10, max_per_document=3)
    # At most 3 from shared_doc
    shared_count = sum(1 for h in result if str(h.parent_chunk.document_id) == str(shared_doc))
    assert shared_count == 3
    # The other doc's hit should also be present
    other_count = sum(1 for h in result if str(h.parent_chunk.document_id) == str(other_doc))
    assert other_count == 1
    assert len(result) == 4


@pytest.mark.asyncio
async def test_scope_fallback_when_category_index_empty():
    section = _section()
    scope_id = str(uuid4())
    hit = _hit("payment terms policy", "pay1", 0.85)
    classification = SectionCategoryResult(
        section_id=section.section_id,
        categories=["payment"],
        query_terms=["payment terms and invoicing"],
    )

    class FakeClient:
        async def list_policy_ids_by_categories(self, *_args, **_kwargs):
            return []

        async def search_policy_recall(self, req):
            assert req.document_ids is not None
            assert str(req.document_ids[0]) == scope_id
            return [hit]

        async def search_policy_fts(self, _req):
            return []

        async def search_policy_by_categories(self, _req, *, categories):
            return []

    settings = ReviewSettings(
        retrieval_max_attempts=1,
        retrieval_scope_fallback_on_category_miss=True,
    )
    bundle = await multi_retrieve_for_section(
        FakeClient(),
        tenant_id="demo",
        section=section,
        contract_type="saas",
        policy_type=None,
        settings=settings,
        classification=classification,
        scope_document_ids=[scope_id],
    )
    assert len(bundle.policy_hits) == 1
    assert (
        bundle.retrieval_meta.get("category_filter_skipped")
        == "scope_fallback_on_category_miss"
    )


@pytest.mark.asyncio
async def test_category_miss_continues_retry_ladder_without_scope():
    section = _section()
    hit = _hit("found on broad retry", "broad1", 0.82)
    recall_calls: list[str] = []

    class FakeClient:
        async def list_policy_ids_by_categories(self, *_args, **_kwargs):
            return []

        async def search_policy_recall(self, req):
            recall_calls.append(req.query)
            return [hit] if recall_calls else []

        async def search_policy_fts(self, _req):
            return []

        async def search_policy_by_categories(self, _req, *, categories):
            return []

    settings = ReviewSettings(
        retrieval_max_attempts=3,
        retrieval_scope_fallback_on_category_miss=False,
        mcp_search_cache_enabled=False,
    )
    bundle = await multi_retrieve_for_section(
        FakeClient(),
        tenant_id="demo",
        section=section,
        contract_type="msa",
        policy_type=None,
        settings=settings,
        classification=_classification(section),
        scope_document_ids=None,
    )
    assert len(bundle.policy_hits) == 1
    assert bundle.retrieval_meta.get("category_filter_miss") is True
    assert len(bundle.retrieval_meta["attempts"]) >= 3
    assert len(recall_calls) == 1
