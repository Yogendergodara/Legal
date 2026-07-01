"""Deterministic catalog recovery when semantic search returns zero candidates (IPC4)."""

from __future__ import annotations

import re

from review_agent.schemas.routing_plan import ObligationRoutingPlan
from review_agent.services.catalog_registry import CatalogEntry
from review_agent.services.section_category_lexical import (
    _CATEGORY_KEYWORDS,
    _MAX_INFERRED_CATEGORIES,
)

_TOKEN_RE = re.compile(r"[a-z0-9]{3,}")

# Taxonomy category → substrings expected in tenant policy titles (deterministic, tenant-scoped).
_CATEGORY_POLICY_TITLE_HINTS: dict[str, tuple[str, ...]] = {
    "privacy": ("privacy", "data processing", "dpa", "personal data"),
    "data_retention": ("privacy", "data processing", "retention"),
    "data_subject_rights": ("privacy", "data processing", "dpa"),
    "breach_notification": ("privacy", "data processing", "incident"),
    "incident_reporting": ("privacy", "incident", "security"),
    "security": ("privacy", "acceptable use", "security"),
    "vendor_security": ("privacy", "acceptable use", "security"),
    "sla": ("product-specific", "advisory", "service"),
    "payment": ("product-specific", "advisory"),
    "ai_usage": ("ai", "artificial intelligence"),
    "compliance": ("acceptable use", "government", "amendment"),
    "governing_law": ("government", "amendment", "general"),
    "ip": ("third-party", "copyright", "trademark", "code"),
    "trademark": ("copyright", "trademark"),
    "confidentiality": ("privacy", "acceptable use", "confidential"),
    "procurement": ("product-specific", "advisory"),
    "employment": ("acceptable use",),
}

# Obligation-text keyword → policy title substrings (high-precision triggers).
_KEYWORD_POLICY_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (r"\bhipaa\b|protected health information|\bphi\b", ("privacy", "data processing", "dpa")),
    (r"service level|\bsla\b", ("product-specific", "advisory")),
    (r"\bai\b|artificial intelligence|machine learning", ("ai",)),
    (r"source code|third[- ]party code", ("third-party", "code")),
    (r"acceptable use", ("acceptable use",)),
    (r"data process|personal data|\bgdpr\b|\bdpa\b", ("privacy", "data processing", "dpa")),
    (r"copyright|trademark", ("copyright", "trademark")),
    (r"government|public sector", ("government", "amendment")),
    (r"advisory services", ("advisory",)),
    (r"subscription|cloud product", ("product-specific",)),
)


def _token_set(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def _token_overlap(text_a: str, text_b: str) -> float:
    a = _token_set(text_a)
    b = _token_set(text_b)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _infer_categories_from_text(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for pattern, category in _CATEGORY_KEYWORDS:
        if category in seen:
            continue
        if re.search(pattern, text, re.IGNORECASE):
            seen.add(category)
            found.append(category)
            if len(found) >= _MAX_INFERRED_CATEGORIES:
                break
    return found


def _title_hints_for_text(text: str, *, plan: ObligationRoutingPlan) -> set[str]:
    hints: set[str] = set()
    blob = " ".join(
        [
            text,
            plan.intent or "",
            " ".join(plan.concepts or []),
            " ".join(plan.search_queries or []),
        ]
    )
    for category in _infer_categories_from_text(blob):
        hints.update(_CATEGORY_POLICY_TITLE_HINTS.get(category, ()))
    for pattern, title_hints in _KEYWORD_POLICY_HINTS:
        if re.search(pattern, blob, re.IGNORECASE):
            hints.update(title_hints)
    return hints


def taxonomy_recovery_candidates(
    *,
    plan: ObligationRoutingPlan,
    obligation_text: str,
    section_title: str,
    catalog_entries: list[CatalogEntry],
    allowed: set[str],
    min_score: float,
    max_candidates: int,
    broad_min_score: float | None = None,
    planner_confidence: float | None = None,
    broad_fence_min_confidence: float = 0.65,
) -> dict[str, float]:
    """Score tenant policies when catalog search + title overlap returned nothing."""
    if not catalog_entries:
        return {}

    query_blob = f"{section_title} {obligation_text}".strip()
    title_hints = _title_hints_for_text(query_blob, plan=plan)
    effective_min = min_score
    if (
        broad_min_score is not None
        and planner_confidence is not None
        and planner_confidence >= broad_fence_min_confidence
    ):
        effective_min = min(effective_min, broad_min_score)

    scored: dict[str, float] = {}
    for entry in catalog_entries:
        doc_id = entry.document_id
        if allowed and doc_id not in allowed:
            continue
        labels = [entry.title, entry.summary, *(entry.aliases or [])]
        lexical = max((_token_overlap(query_blob, label) for label in labels if label), default=0.0)
        hint_boost = 0.0
        title_lower = entry.title.lower()
        if title_hints:
            hits = sum(1 for hint in title_hints if hint in title_lower)
            if hits:
                hint_boost = min(0.35, 0.12 * hits)
        score = round(min(1.0, lexical + hint_boost), 4)
        if score >= effective_min:
            scored[doc_id] = max(scored.get(doc_id, 0.0), score)

    if not scored:
        return {}

    cap = max_candidates if max_candidates > 0 else len(scored)
    sorted_ids = sorted(scored.keys(), key=lambda doc_id: scored[doc_id], reverse=True)
    return {doc_id: scored[doc_id] for doc_id in sorted_ids[:cap]}
