"""Select policy hits for compare prompt (Phase 22 P4)."""

from __future__ import annotations

from typing import Literal

from document_core.schemas.chunk import RetrievalHit
from document_core.schemas.taxonomy import normalize_categories
from review_agent.config import ReviewSettings, get_settings
from review_agent.services.retrieval_relevance import score_hit_relevance

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
    section_cats = set(normalize_categories(section_categories or []))
    if section_cats:
        aligned = [h for h in hits if section_cats.intersection(_hit_categories(h))]
        if aligned:
            scored = [
                (
                    hit,
                    score_hit_relevance(
                        hit,
                        section_categories=section_categories,
                        section_title=section_title,
                    ),
                )
                for hit in aligned
            ]
            min_score = cfg.compare_hit_min_relevance_score
            filtered = [(hit, score) for hit, score in scored if score >= min_score]
            if filtered:
                filtered.sort(key=lambda pair: pair[1], reverse=True)
                return [hit for hit, _ in filtered[:cap]]
            return hits[:1]
    return hits[:1]


def filter_hits_for_compare(
    hits_by_section: dict[str, list[RetrievalHit]],
    categories_by_section: dict[str, list[str]] | None,
    *,
    section_titles_by_id: dict[str, str] | None = None,
    settings: ReviewSettings | None = None,
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
        )
        hits_out += len(selected)
        if not hits:
            filtered[section_id] = []
            continue
        if cfg.compare_policy_hit_mode == "category_aligned" and section_cats:
            section_cat_set = set(normalize_categories(section_cats))
            if any(section_cat_set.intersection(_hit_categories(h)) for h in selected):
                category_aligned += 1
            else:
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
