"""Select policy hits for compare prompt (Phase 22 P4)."""

from __future__ import annotations

import logging
from typing import Literal

from document_core.schemas.chunk import RetrievalHit
from document_core.schemas.taxonomy import normalize_categories
from review_agent.config import ReviewSettings, get_settings
from review_agent.services.retrieval_relevance import (
    _specific_categories,
    filter_on_topic_hits,
    has_specific_category_overlap,
    is_incompatible_hit,
)

ComparePolicyHitMode = Literal["all_top_k", "category_aligned", "primary_only"]

logger = logging.getLogger(__name__)


def _hit_categories(hit: RetrievalHit) -> list[str]:
    parent = hit.parent_chunk
    raw: list[str] = []
    meta_cats = (parent.metadata or {}).get("categories")
    if isinstance(meta_cats, list):
        raw = [str(c) for c in meta_cats]
    return normalize_categories(raw)


def _trusted_gate_compatible_hits(
    hits: list[RetrievalHit],
    *,
    section_categories: list[str],
    section_title: str,
    cap: int,
    doc_catalog_categories: dict[str, list[str]] | None = None,
) -> list[RetrievalHit]:
    compatible = [
        hit
        for hit in hits
        if not is_incompatible_hit(
            section_categories,
            section_title,
            hit,
            doc_catalog_categories=doc_catalog_categories,
        )
    ]
    return compatible[:cap]


def select_compare_hits(
    hits: list[RetrievalHit],
    *,
    section_categories: list[str],
    section_title: str = "",
    settings: ReviewSettings | None = None,
    doc_catalog_categories: dict[str, list[str]] | None = None,
    retrieval_gate_applied: bool = False,
) -> tuple[list[RetrievalHit], bool]:
    """Return (policy hits for compare prompt, used_trusted_gate_fallback)."""
    cfg = settings or get_settings()
    if not hits:
        return [], False

    mode = cfg.compare_policy_hit_mode
    if mode == "primary_only":
        return hits[:1], False

    if mode == "all_top_k":
        cap = max(1, cfg.retrieval_final_top_k)
        return hits[:cap], False

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
    if relevant:
        return relevant[:cap], False

    if (
        retrieval_gate_applied
        and cfg.retrieval_coverage_filter_aligned
        and cfg.compare_hit_trust_retrieval_gate
    ):
        trusted = _trusted_gate_compatible_hits(
            hits,
            section_categories=section_categories,
            section_title=section_title,
            cap=cap,
            doc_catalog_categories=doc_catalog_categories,
        )
        if trusted:
            return trusted, True
    return [], False


def filter_hits_for_compare(
    hits_by_section: dict[str, list[RetrievalHit]],
    categories_by_section: dict[str, list[str]] | None,
    *,
    section_titles_by_id: dict[str, str] | None = None,
    settings: ReviewSettings | None = None,
    doc_catalog_categories: dict[str, list[str]] | None = None,
    retrieval_gate_applied_by_section: dict[str, bool] | None = None,
    allowed_document_ids: set[str] | None = None,
) -> tuple[dict[str, list[RetrievalHit]], dict[str, int | float | str]]:
    """Filter each section's hits; return stats for ops metadata."""
    cfg = settings or get_settings()
    categories_by_section = categories_by_section or {}
    titles = section_titles_by_id or {}
    gate_flags = retrieval_gate_applied_by_section or {}
    filtered: dict[str, list[RetrievalHit]] = {}
    category_aligned = 0
    fallback_primary = 0
    trusted_gate_fallback = 0
    selection_empty_with_hits = 0
    hits_in = 0
    hits_out = 0

    for section_id, hits in hits_by_section.items():
        if allowed_document_ids:
            hits = [
                hit
                for hit in hits
                if str(hit.parent_chunk.document_id) in allowed_document_ids
            ]
        hits_in += len(hits)
        section_cats = categories_by_section.get(section_id, [])
        selected, used_trusted = select_compare_hits(
            hits,
            section_categories=section_cats,
            section_title=titles.get(section_id, section_id),
            settings=cfg,
            doc_catalog_categories=doc_catalog_categories,
            retrieval_gate_applied=bool(gate_flags.get(section_id)),
        )
        hits_out += len(selected)
        if not hits:
            filtered[section_id] = []
            continue
        if used_trusted:
            trusted_gate_fallback += 1
        if hits and not selected:
            selection_empty_with_hits += 1
        if cfg.compare_policy_hit_mode == "category_aligned" and section_cats:
            if selected and not used_trusted and (
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
                if not cfg.compare_hit_allow_primary_fallback:
                    logger.warning(
                        "compare_hit_selection: section %s had %d hit(s) but none on-topic; "
                        "compare skipped",
                        section_id,
                        len(hits),
                    )
                fallback_primary += 1
        filtered[section_id] = selected

    avg_hits = round(hits_out / len(hits_by_section), 2) if hits_by_section else 0.0
    stats: dict[str, int | float | str] = {
        "mode": cfg.compare_policy_hit_mode,
        "avg_hits_per_section": avg_hits,
        "category_aligned_sections": category_aligned,
        "fallback_primary_sections": fallback_primary,
        "trusted_gate_fallback_sections": trusted_gate_fallback,
        "selection_empty_with_hits": selection_empty_with_hits,
        "hits_in": hits_in,
        "hits_out": hits_out,
    }
    return filtered, stats
