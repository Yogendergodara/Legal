"""Select policy hits for compare prompt (Phase 22 P4)."""

from __future__ import annotations

from typing import Literal

from document_core.schemas.chunk import RetrievalHit
from document_core.schemas.taxonomy import normalize_categories
from review_agent.config import ReviewSettings, get_settings
from review_agent.services.retrieval_relevance import (
    _specific_categories,
    filter_on_topic_hits,
    has_specific_category_overlap,
)

ComparePolicyHitMode = Literal["all_top_k", "category_aligned", "primary_only"]


def _hit_categories(hit: RetrievalHit) -> list[str]:
    parent = hit.parent_chunk
    raw: list[str] = []
    meta_cats = (parent.metadata or {}).get("categories")
    if isinstance(meta_cats, list):
        raw = [str(c) for c in meta_cats]
    return normalize_categories(raw)


def select_compare_hits(
    hits: list[RetrievalHit],
    *,
    section_categories: list[str],
    section_title: str = "",
    settings: ReviewSettings | None = None,
    doc_catalog_categories: dict[str, list[str]] | None = None,
) -> list[RetrievalHit]:
    """Return policy hits to include in compare prompt (post-rerank order preserved)."""
    cfg = settings or get_settings()
    if not hits:
        return []

    mode = cfg.compare_policy_hit_mode
    if mode == "primary_only":
        return hits[:1]

    if mode == "all_top_k":
        cap = max(1, cfg.retrieval_final_top_k)
        return hits[:cap]

    cap = max(1, cfg.compare_max_policy_hits)
    require_overlap = bool(_specific_categories(section_categories))
    relevant, _dropped, _reason = filter_on_topic_hits(
        hits,
        section_categories=section_categories,
        section_title=section_title,
        min_score=cfg.compare_hit_min_relevance_score,
        doc_catalog_categories=doc_catalog_categories,
        keep_best_fallback=cfg.compare_hit_allow_primary_fallback,
        require_specific_overlap=require_overlap,
    )
    return relevant[:cap]


def filter_hits_for_compare(
    hits_by_section: dict[str, list[RetrievalHit]],
    categories_by_section: dict[str, list[str]] | None,
    *,
    section_titles_by_id: dict[str, str] | None = None,
    settings: ReviewSettings | None = None,
    doc_catalog_categories: dict[str, list[str]] | None = None,
) -> tuple[dict[str, list[RetrievalHit]], dict[str, int | float | str]]:
    """Filter each section's hits; return stats for ops metadata."""
    cfg = settings or get_settings()
    categories_by_section = categories_by_section or {}
    titles = section_titles_by_id or {}
    filtered: dict[str, list[RetrievalHit]] = {}
    category_aligned = 0
    fallback_primary = 0
    hits_in = 0
    hits_out = 0

    for section_id, hits in hits_by_section.items():
        hits_in += len(hits)
        section_cats = categories_by_section.get(section_id, [])
        selected = select_compare_hits(
            hits,
            section_categories=section_cats,
            section_title=titles.get(section_id, section_id),
            settings=cfg,
            doc_catalog_categories=doc_catalog_categories,
        )
        hits_out += len(selected)
        if not hits:
            filtered[section_id] = []
            continue
        if cfg.compare_policy_hit_mode == "category_aligned" and section_cats:
            if selected and (
                not _specific_categories(section_cats)
                or any(
                    has_specific_category_overlap(
                        section_cats,
                        h,
                        doc_catalog_categories=doc_catalog_categories,
                    )
                    for h in selected
                )
            ):
                category_aligned += 1
            elif hits and not selected:
                fallback_primary += 1
        filtered[section_id] = selected

    avg_hits = round(hits_out / len(hits_by_section), 2) if hits_by_section else 0.0
    stats: dict[str, int | float | str] = {
        "mode": cfg.compare_policy_hit_mode,
        "avg_hits_per_section": avg_hits,
        "category_aligned_sections": category_aligned,
        "fallback_primary_sections": fallback_primary,
        "hits_in": hits_in,
        "hits_out": hits_out,
    }
    return filtered, stats
