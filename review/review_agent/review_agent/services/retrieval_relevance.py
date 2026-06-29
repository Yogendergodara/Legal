"""Post-retrieval relevance filter — drop off-topic policy chunks."""

from __future__ import annotations

import re
from typing import Any, Literal

from document_core.schemas.chunk import RetrievalHit
from document_core.schemas.taxonomy import BROAD_POLICY_CATEGORIES, normalize_categories
from review_agent.config import ReviewSettings
from review_agent.services.section_gap_status import normalize_section_title

# Ingest cap treats security as broad; relevance keeps security as a match signal for security sections.
_BROAD_CATEGORIES = BROAD_POLICY_CATEGORIES - {"security"}
_TOKEN_RE = re.compile(r"[a-z0-9]{3,}")

_INCIDENT_POLICY_CATEGORIES = frozenset(
    {
        "incident_reporting",
        "breach_notification",
        "records_management",
        "business_continuity",
    }
)

_INCOMPATIBLE_SECTION_CATEGORIES: tuple[tuple[frozenset[str], frozenset[str]], ...] = (
    (
        frozenset({"governing_law"}),
        _INCIDENT_POLICY_CATEGORIES,
    ),
)

_NOTICE_TITLE = re.compile(r"^(notices?|notice provisions?)\b", re.IGNORECASE)
_PREAMBLE_SECTION_IDS = frozenset({"preamble", "preface", "introduction"})


def _is_preamble_hit(hit: RetrievalHit) -> bool:
    section_id = (hit.parent_chunk.section_id or "").lower().strip()
    if section_id in _PREAMBLE_SECTION_IDS:
        return True
    return section_id.startswith("preamble")


def _hit_categories(hit: RetrievalHit) -> list[str]:
    raw = (hit.parent_chunk.metadata or {}).get("categories")
    if isinstance(raw, list):
        return normalize_categories([str(c) for c in raw])
    return []


def _effective_hit_categories(
    hit: RetrievalHit,
    doc_catalog_categories: dict[str, list[str]] | None = None,
) -> list[str]:
    chunk_cats = _hit_categories(hit)
    if not doc_catalog_categories:
        return chunk_cats
    doc_cats = doc_catalog_categories.get(str(hit.parent_chunk.document_id), [])
    if not doc_cats:
        return chunk_cats
    return normalize_categories(chunk_cats + [str(c) for c in doc_cats])


def _specific_categories(categories: list[str]) -> set[str]:
    return {c for c in normalize_categories(categories) if c not in _BROAD_CATEGORIES}


def _title_tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def has_specific_category_overlap(
    section_categories: list[str],
    hit: RetrievalHit,
    *,
    doc_catalog_categories: dict[str, list[str]] | None = None,
) -> bool:
    """True when section has no specific categories, or hit shares at least one."""
    section_specific = _specific_categories(section_categories)
    if not section_specific:
        return True
    hit_specific = _specific_categories(
        _effective_hit_categories(hit, doc_catalog_categories)
    )
    return bool(section_specific & hit_specific)


def is_incompatible_hit(
    section_categories: list[str],
    section_title: str,
    hit: RetrievalHit,
    *,
    doc_catalog_categories: dict[str, list[str]] | None = None,
) -> bool:
    """Block known cross-family pairings (e.g. governing law vs incident response)."""
    section_specific = _specific_categories(section_categories)
    hit_cats = set(_effective_hit_categories(hit, doc_catalog_categories))
    for blocked_sections, blocked_policies in _INCOMPATIBLE_SECTION_CATEGORIES:
        if section_specific & blocked_sections and hit_cats & blocked_policies:
            return True
    title = normalize_section_title(section_title or "")
    if title and _NOTICE_TITLE.search(title) and hit_cats & _INCIDENT_POLICY_CATEGORIES:
        return True
    return False


def score_hit_relevance(
    hit: RetrievalHit,
    *,
    section_categories: list[str],
    section_title: str,
    doc_catalog_categories: dict[str, list[str]] | None = None,
) -> float:
    """Higher = more likely on-topic. 0 = no alignment signal."""
    if is_incompatible_hit(
        section_categories,
        section_title,
        hit,
        doc_catalog_categories=doc_catalog_categories,
    ):
        return 0.0

    section_specific = _specific_categories(section_categories)
    hit_specific = _specific_categories(
        _effective_hit_categories(hit, doc_catalog_categories)
    )
    score = 0.0
    if section_specific and hit_specific:
        overlap = len(section_specific & hit_specific)
        if overlap >= 2:
            score += 1.0
        elif overlap == 1:
            score += 0.6
    elif section_specific and not hit_specific:
        score += 0.0
    else:
        score += 0.3

    parent = hit.parent_chunk
    title_tokens = _title_tokens(f"{parent.title} {parent.section_id}")
    section_tokens = _title_tokens(section_title)
    if title_tokens and section_tokens:
        shared = len(title_tokens & section_tokens)
        if shared >= 2:
            score += 0.4
        elif shared == 1:
            score += 0.2
    if not section_specific:
        from review_agent.config import get_settings

        if get_settings().retrieval_penalize_preamble_general and _is_preamble_hit(hit):
            shared = len(title_tokens & section_tokens) if title_tokens and section_tokens else 0
            if shared < 1:
                score = min(score, 0.25)
    return score


def coverage_block_reason(
    section_categories: list[str],
    section_title: str,
    hits: list[RetrievalHit],
    *,
    doc_catalog_categories: dict[str, list[str]] | None = None,
) -> str:
    """Reason code when compare must be skipped for retrieved hits."""
    if not hits:
        return "no_relevant_policy_hits"
    if all(
        is_incompatible_hit(
            section_categories,
            section_title,
            hit,
            doc_catalog_categories=doc_catalog_categories,
        )
        for hit in hits
    ):
        if _NOTICE_TITLE.search(normalize_section_title(section_title or "")):
            return "notice_vs_incident_mismatch"
        return "incompatible_policy_family"
    return "no_relevant_policy_hits"


def filter_on_topic_hits(
    hits: list[RetrievalHit],
    *,
    section_categories: list[str],
    section_title: str,
    min_score: float,
    doc_catalog_categories: dict[str, list[str]] | None = None,
    keep_best_fallback: bool = False,
    require_specific_overlap: bool = True,
    fallback_on_overlap_miss: bool = False,
) -> tuple[list[RetrievalHit], list[RetrievalHit], str]:
    """Return (relevant_hits, dropped_hits, block_reason). block_reason empty when relevant non-empty."""
    if not hits:
        return [], [], "no_relevant_policy_hits"

    if all(
        is_incompatible_hit(
            section_categories,
            section_title,
            hit,
            doc_catalog_categories=doc_catalog_categories,
        )
        for hit in hits
    ):
        return (
            [],
            list(hits),
            coverage_block_reason(
                section_categories,
                section_title,
                hits,
                doc_catalog_categories=doc_catalog_categories,
            ),
        )

    scored: list[tuple[RetrievalHit, float]] = []
    for hit in hits:
        if is_incompatible_hit(
            section_categories,
            section_title,
            hit,
            doc_catalog_categories=doc_catalog_categories,
        ):
            continue
        score = score_hit_relevance(
            hit,
            section_categories=section_categories,
            section_title=section_title,
            doc_catalog_categories=doc_catalog_categories,
        )
        if score >= min_score:
            scored.append((hit, score))

    if require_specific_overlap and _specific_categories(section_categories):
        overlap_scored = [
            pair
            for pair in scored
            if has_specific_category_overlap(
                section_categories,
                pair[0],
                doc_catalog_categories=doc_catalog_categories,
            )
        ]
        if scored and not overlap_scored:
            if keep_best_fallback and fallback_on_overlap_miss:
                scored = []
            else:
                return [], list(hits), "no_specific_category_overlap"
        else:
            scored = overlap_scored

    if not scored:
        if keep_best_fallback:
            fallback_scored = [
                (
                    hit,
                    score_hit_relevance(
                        hit,
                        section_categories=section_categories,
                        section_title=section_title,
                        doc_catalog_categories=doc_catalog_categories,
                    ),
                )
                for hit in hits
                if not is_incompatible_hit(
                    section_categories,
                    section_title,
                    hit,
                    doc_catalog_categories=doc_catalog_categories,
                )
            ]
            if fallback_scored:
                best_hit, _ = max(fallback_scored, key=lambda pair: pair[1])
                dropped = [hit for hit in hits if hit is not best_hit]
                return [best_hit], dropped, ""
        reason = (
            "all_hits_below_relevance_floor"
            if not require_specific_overlap or not _specific_categories(section_categories)
            else "no_specific_category_overlap"
        )
        return [], list(hits), reason

    scored.sort(key=lambda pair: pair[1], reverse=True)
    relevant = [hit for hit, _ in scored]
    relevant_ids = {id(hit) for hit in relevant}
    dropped = [hit for hit in hits if id(hit) not in relevant_ids]
    return relevant, dropped, ""


def filter_hits_by_relevance(
    hits: list[RetrievalHit],
    *,
    section_categories: list[str],
    section_title: str,
    min_score: float = 0.2,
    keep_best_fallback: bool = False,
    doc_catalog_categories: dict[str, list[str]] | None = None,
    require_specific_overlap: bool = False,
    fallback_on_overlap_miss: bool = False,
) -> tuple[list[RetrievalHit], list[RetrievalHit]]:
    """Return (relevant_hits, dropped_hits)."""
    relevant, dropped, _reason = filter_on_topic_hits(
        hits,
        section_categories=section_categories,
        section_title=section_title,
        min_score=min_score,
        doc_catalog_categories=doc_catalog_categories,
        keep_best_fallback=keep_best_fallback,
        require_specific_overlap=require_specific_overlap,
        fallback_on_overlap_miss=fallback_on_overlap_miss,
    )
    return relevant, dropped


def relevance_filter_kwargs(
    cfg: ReviewSettings,
    *,
    stage: Literal["retrieval", "coverage"],
) -> dict[str, Any]:
    """Shared relevance filter params — single overlap source (Phase DF-1)."""
    overlap = cfg.policy_coverage_require_specific_overlap
    if stage == "retrieval":
        return {
            "min_score": cfg.retrieval_relevance_min_score,
            "keep_best_fallback": cfg.retrieval_relevance_keep_best_fallback,
            "require_specific_overlap": overlap,
        }
    return {
        "min_score": max(
            cfg.retrieval_relevance_min_score,
            cfg.compare_hit_min_relevance_score,
        ),
        "keep_best_fallback": cfg.retrieval_relevance_keep_best_fallback,
        "require_specific_overlap": overlap,
    }
