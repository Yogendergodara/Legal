"""Ingest-driven alias matching for explicit policy mentions (Phase R2)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from review_agent.services.catalog_registry import CatalogEntry

_NORM_RE = re.compile(r"[^a-z0-9\s]+")


def _normalize(text: str) -> str:
    return " ".join(_NORM_RE.sub(" ", (text or "").lower()).split())


def _mention_score(mention: str, candidate: str) -> float:
    m = _normalize(mention)
    c = _normalize(candidate)
    if not m or not c:
        return 0.0
    if m == c:
        return 1.0
    if m in c or c in m:
        return 0.95
    return 0.0


@dataclass(frozen=True)
class AliasMatchResult:
    document_id: str
    policy_ref: str
    title: str
    confidence: float
    matched_mention: str


def match_explicit_mentions(
    mentions: list[str],
    catalog_entries: list[CatalogEntry],
    *,
    min_score: float = 0.92,
) -> AliasMatchResult | None:
    """Return best single doc match when confidence >= min_score."""
    if not mentions or not catalog_entries:
        return None

    best: AliasMatchResult | None = None
    tie = False
    for mention in mentions:
        mention = (mention or "").strip()
        if not mention:
            continue
        for entry in catalog_entries:
            candidates = [entry.title, *entry.aliases]
            for candidate in candidates:
                score = _mention_score(mention, candidate)
                if score < min_score:
                    continue
                match = AliasMatchResult(
                    document_id=entry.document_id,
                    policy_ref=entry.policy_ref,
                    title=entry.title,
                    confidence=score,
                    matched_mention=mention,
                )
                if best is None or score > best.confidence:
                    best = match
                    tie = False
                elif best and score == best.confidence and entry.document_id != best.document_id:
                    tie = True

    if best is None:
        return None
    if tie:
        return AliasMatchResult(
            document_id=best.document_id,
            policy_ref=best.policy_ref,
            title=best.title,
            confidence=0.75,
            matched_mention=best.matched_mention,
        )
    return best
