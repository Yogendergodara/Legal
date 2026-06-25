"""Deterministic dedupe and per-section cap for compare items (Phase 21 P1-D)."""

from __future__ import annotations

import re
from collections import defaultdict

from document_core.schemas.compliance import ComplianceStatus, Severity
from review_agent.config import ReviewSettings, get_settings
from review_agent.schemas.section_compare import SectionCompareItem

_QUOTE_OVERLAP_THRESHOLD = 0.6
_SEVERITY_RANK = {
    Severity.CRITICAL: 3,
    Severity.IMPORTANT: 2,
    Severity.INFO: 1,
}

_DIMENSION_ALIASES: dict[str, frozenset[str]] = {
    "secure deletion": frozenset(
        {"secure deletion", "secure deletion requirements", "deletion", "destruction"}
    ),
    "data principal rights": frozenset(
        {"data principal rights", "data subject rights", "gdpr", "dpdpa", "erasure"}
    ),
    "code of conduct": frozenset(
        {"code of conduct", "conduct acknowledgment", "conduct principles"}
    ),
    "security measures": frozenset(
        {"security measures", "encryption", "access control", "mfa"}
    ),
    "data retention": frozenset(
        {"data retention", "retention period", "retention requirements"}
    ),
}


def normalize_dimension_label(label: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    cleaned = re.sub(r"[^\w\s]", " ", (label or "").strip().lower())
    return " ".join(cleaned.split())


def dimension_topic_key(label: str) -> str:
    """Cluster dimension labels that refer to the same compliance topic."""
    normalized = normalize_dimension_label(label)
    if not normalized:
        return ""
    for topic, aliases in _DIMENSION_ALIASES.items():
        if any(alias in normalized for alias in aliases):
            return topic
    return normalized


def _normalize_quote(quote: str) -> str:
    return " ".join((quote or "").strip().lower().split())


def _quote_tokens(quote: str) -> set[str]:
    return set(_normalize_quote(quote).split())


def _same_quote_anchor(left: str, right: str) -> bool:
    normalized_left = _normalize_quote(left)
    normalized_right = _normalize_quote(right)
    if not normalized_left or not normalized_right:
        return normalized_left == normalized_right
    return (
        normalized_left == normalized_right
        or normalized_left in normalized_right
        or normalized_right in normalized_left
    )


def _quotes_overlap(left: str, right: str, *, threshold: float = _QUOTE_OVERLAP_THRESHOLD) -> bool:
    if _same_quote_anchor(left, right):
        return True
    tokens_left = _quote_tokens(left)
    tokens_right = _quote_tokens(right)
    if not tokens_left or not tokens_right:
        return False
    union = len(tokens_left | tokens_right)
    if union == 0:
        return False
    return len(tokens_left & tokens_right) / union >= threshold


def _severity_rank(severity: Severity) -> int:
    return _SEVERITY_RANK.get(severity, 0)


def _confidence(item: SectionCompareItem) -> float:
    if item.confidence is None:
        return 0.5
    return float(item.confidence)


def _better_item(left: SectionCompareItem, right: SectionCompareItem) -> SectionCompareItem:
    if _severity_rank(left.severity) != _severity_rank(right.severity):
        return left if _severity_rank(left.severity) > _severity_rank(right.severity) else right
    if _confidence(left) != _confidence(right):
        return left if _confidence(left) > _confidence(right) else right
    if len(left.policy_quote or "") != len(right.policy_quote or ""):
        return (
            left
            if len(left.policy_quote or "") >= len(right.policy_quote or "")
            else right
        )
    return left


def _exact_key(item: SectionCompareItem) -> tuple[str, str, str]:
    return (
        item.section_id,
        item.policy_document_id or "",
        normalize_dimension_label(item.dimension_label or item.section_id),
    )


def is_gap_compare_item(item: SectionCompareItem) -> bool:
    if item.status == ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT:
        return True
    return (item.rationale or "").startswith("Section compare failed:")


def _should_merge(existing: SectionCompareItem, candidate: SectionCompareItem, *, across_policies: bool) -> bool:
    if existing.section_id != candidate.section_id:
        return False
    if existing.status != candidate.status:
        return False
    if _exact_key(existing) == _exact_key(candidate):
        return True
    if _same_quote_anchor(existing.contract_quote, candidate.contract_quote):
        return True
    if not across_policies:
        return False
    same_label = normalize_dimension_label(existing.dimension_label or existing.section_id) == (
        normalize_dimension_label(candidate.dimension_label or candidate.section_id)
    )
    return same_label and _quotes_overlap(existing.contract_quote, candidate.contract_quote)


def dedupe_compare_items(
    items: list[SectionCompareItem],
    *,
    across_policies: bool = True,
) -> tuple[list[SectionCompareItem], int]:
    """Return deduped items and count removed."""
    gaps = [item for item in items if is_gap_compare_item(item)]
    kept: list[SectionCompareItem] = []
    removed = 0

    for item in items:
        if is_gap_compare_item(item):
            continue
        merged = False
        for index, existing in enumerate(kept):
            if not _should_merge(existing, item, across_policies=across_policies):
                continue
            kept[index] = _better_item(existing, item)
            removed += 1
            merged = True
            break
        if not merged:
            kept.append(item)

    return gaps + kept, removed


def _cap_sort_key(item: SectionCompareItem) -> tuple[int, int, float]:
    non_compliant = 1 if item.status == ComplianceStatus.NON_COMPLIANT else 0
    return (non_compliant, _severity_rank(item.severity), _confidence(item))


def cap_compare_items_by_section(
    items: list[SectionCompareItem],
    max_per_section: int,
) -> tuple[list[SectionCompareItem], int, list[str]]:
    """Cap compare items per section; never drop CRITICAL NON_COMPLIANT."""
    if max_per_section <= 0:
        return items, 0, []

    gaps = [item for item in items if is_gap_compare_item(item)]
    work = [item for item in items if not is_gap_compare_item(item)]
    by_section: dict[str, list[SectionCompareItem]] = defaultdict(list)
    for item in work:
        by_section[item.section_id].append(item)

    warnings: list[str] = []
    capped_total = 0
    kept_work: list[SectionCompareItem] = []

    for section_id, group in by_section.items():
        critical_nc = [
            item
            for item in group
            if item.status == ComplianceStatus.NON_COMPLIANT
            and item.severity == Severity.CRITICAL
        ]
        remainder = [item for item in group if item not in critical_nc]
        remainder.sort(key=_cap_sort_key, reverse=True)

        target = max(max_per_section, len(critical_nc))
        kept = list(critical_nc)
        for item in remainder:
            if len(kept) >= target:
                break
            kept.append(item)

        dropped = len(group) - len(kept)
        if dropped:
            capped_total += dropped
            warnings.append(
                f"section {section_id}: capped {dropped} finding(s) (max {max_per_section})"
            )
        kept_work.extend(kept)

    return gaps + kept_work, capped_total, warnings


def suppress_contradicted_non_compliant(
    items: list[SectionCompareItem],
    *,
    settings: ReviewSettings | None = None,
) -> tuple[list[SectionCompareItem], int]:
    """Drop NON_COMPLIANT when same dimension is COMPLIANT elsewhere (cross-section)."""
    cfg = settings or get_settings()
    by_dimension: dict[str, list[SectionCompareItem]] = defaultdict(list)
    for item in items:
        if is_gap_compare_item(item):
            continue
        label = item.dimension_label or item.section_id
        key = (
            dimension_topic_key(label)
            if cfg.finding_dedupe_topic_cluster
            else normalize_dimension_label(label)
        )
        by_dimension[key].append(item)

    drop_ids: set[int] = set()
    for group in by_dimension.values():
        compliant = [
            item
            for item in group
            if item.status == ComplianceStatus.COMPLIANT and (item.contract_quote or "").strip()
        ]
        if not compliant:
            continue
        for item in group:
            if item.status == ComplianceStatus.NON_COMPLIANT:
                drop_ids.add(id(item))

    if not drop_ids:
        return items, 0
    kept = [item for item in items if id(item) not in drop_ids]
    return kept, len(drop_ids)


def prepare_compare_items_for_merge(
    items: list[SectionCompareItem],
    *,
    settings: ReviewSettings | None = None,
) -> tuple[list[SectionCompareItem], int, int, list[str]]:
    """Dedupe then cap compare items before merge."""
    cfg = settings or get_settings()
    items, contradiction_removed = suppress_contradicted_non_compliant(items, settings=cfg)
    equivalence_removed = 0
    if cfg.equivalence_guard_enabled:
        from review_agent.services.equivalence_guard import apply_equivalence_guard

        items, equivalence_removed = apply_equivalence_guard(items)
    deduped, dedupe_removed = dedupe_compare_items(
        items,
        across_policies=cfg.finding_dedupe_across_policies,
    )
    capped, cap_removed, cap_warnings = cap_compare_items_by_section(
        deduped,
        cfg.section_compare_max_findings_per_section,
    )
    warnings: list[str] = []
    if contradiction_removed:
        warnings.append(
            f"suppressed {contradiction_removed} contradicted NON_COMPLIANT finding(s)"
        )
    if equivalence_removed:
        warnings.append(
            f"equivalence guard upgraded {equivalence_removed} finding(s)"
        )
    if dedupe_removed:
        warnings.append(f"deduped {dedupe_removed} duplicate compare item(s)")
    warnings.extend(cap_warnings)
    return capped, dedupe_removed, cap_removed, warnings
