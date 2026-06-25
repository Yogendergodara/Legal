"""Review confidence metrics for assessment export (Phase E3)."""

from __future__ import annotations

from collections import Counter
from typing import Any

_QUOTE_VALIDATE_DOWNGRADE = "Downgraded: model quotes were not exact substrings"
_STATUS_RANK = {
    "NON_COMPLIANT": 0,
    "INSUFFICIENT_POLICY_CONTEXT": 1,
    "INCONCLUSIVE": 2,
    "COMPLIANT": 3,
}


def _field(finding: Any, name: str, default: Any = "") -> Any:
    if isinstance(finding, dict):
        return finding.get(name, default)
    return getattr(finding, name, default)


def _status_value(finding: Any) -> str:
    status = _field(finding, "status", "")
    if hasattr(status, "value"):
        return str(status.value)
    return str(status or "")


def compute_review_confidence_metrics(
    findings: list[Any],
    *,
    sections_total: int | None = None,
) -> dict[str, float | int]:
    """Aggregate section-level confidence and downgrade attribution."""
    by_section: dict[str, str] = {}
    downgrade_quote_validate = 0
    downgrade_grounding = 0

    for finding in findings:
        sid = str(_field(finding, "contract_section_id", "") or "").strip()
        if not sid:
            continue
        status_val = _status_value(finding)
        rationale = str(_field(finding, "rationale", "") or "")
        meta = _field(finding, "metadata", {}) or {}
        if not isinstance(meta, dict):
            meta = {}

        if _QUOTE_VALIDATE_DOWNGRADE in rationale:
            downgrade_quote_validate += 1
        if meta.get("grounding_failed") is True:
            downgrade_grounding += 1

        prev = by_section.get(sid)
        if prev is None or _STATUS_RANK.get(status_val, 99) < _STATUS_RANK.get(prev, 99):
            by_section[sid] = status_val

    counts = Counter(by_section.values())
    total = sections_total if sections_total and sections_total > 0 else len(by_section)
    if total <= 0:
        return {
            "sections_total": 0,
            "inconclusive_section_pct": 0.0,
            "ipc_section_pct": 0.0,
            "confident_section_pct": 0.0,
            "downgrade_quote_validate": downgrade_quote_validate,
            "downgrade_grounding": downgrade_grounding,
        }

    inconclusive = counts.get("INCONCLUSIVE", 0)
    ipc = counts.get("INSUFFICIENT_POLICY_CONTEXT", 0)
    confident = counts.get("COMPLIANT", 0) + counts.get("NON_COMPLIANT", 0)

    return {
        "sections_total": total,
        "inconclusive_section_pct": round(100 * inconclusive / total, 1),
        "ipc_section_pct": round(100 * ipc / total, 1),
        "confident_section_pct": round(100 * confident / total, 1),
        "downgrade_quote_validate": downgrade_quote_validate,
        "downgrade_grounding": downgrade_grounding,
    }
