"""Post-retrieval relevance filter — drop off-topic policy chunks."""

from __future__ import annotations

import re

from document_core.schemas.chunk import RetrievalHit
from document_core.schemas.taxonomy import BROAD_POLICY_CATEGORIES, normalize_categories

# Ingest cap treats security as broad; relevance keeps security as a match signal for security sections.
_BROAD_CATEGORIES = BROAD_POLICY_CATEGORIES - {"security"}
_TOKEN_RE = re.compile(r"[a-z0-9]{3,}")


def _hit_categories(hit: RetrievalHit) -> list[str]:
    raw = (hit.parent_chunk.metadata or {}).get("categories")
    if isinstance(raw, list):
        return normalize_categories([str(c) for c in raw])
    return []


def _specific_categories(categories: list[str]) -> set[str]:
    return {c for c in normalize_categories(categories) if c not in _BROAD_CATEGORIES}


def _title_tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def score_hit_relevance(
    hit: RetrievalHit,
    *,
    section_categories: list[str],
    section_title: str,
) -> float:
    """Higher = more likely on-topic. 0 = no alignment signal."""
    section_specific = _specific_categories(section_categories)
    hit_specific = _specific_categories(_hit_categories(hit))
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
    return score


def filter_hits_by_relevance(
    hits: list[RetrievalHit],
    *,
    section_categories: list[str],
    section_title: str,
    min_score: float = 0.2,
) -> tuple[list[RetrievalHit], list[RetrievalHit]]:
    """Return (relevant_hits, dropped_hits). Keeps at least one hit if any exist."""
    if not hits:
        return [], []
    scored = [
        (hit, score_hit_relevance(hit, section_categories=section_categories, section_title=section_title))
        for hit in hits
    ]
    relevant = [hit for hit, score in scored if score >= min_score]
    dropped = [hit for hit, score in scored if score < min_score]
    if not relevant:
        best = max(scored, key=lambda pair: pair[1])
        return [best[0]], dropped
    return relevant, dropped
