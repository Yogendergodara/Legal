"""Gap-row status semantics: boilerplate vs reviewed-with-gap (Phase 22 P3)."""

from __future__ import annotations

import re
from typing import Literal

from document_core.schemas.chunk import IndexedChunk
from document_core.schemas.compliance import ComplianceFinding, ComplianceStatus
from review_agent.config import ReviewSettings, get_settings

ReviewOutcome = Literal[
    "boilerplate",
    "playbook_gap",
    "pipeline_incomplete",
    "compare_failed",
    "contract_reviewed",
]

_GOVERNING_LAW_TITLE = re.compile(r"^governing law\b", re.IGNORECASE)
_SECTION_PREFIX = re.compile(r"^[\d]+(?:\.[\d]+)*\s+")

_BOILERPLATE_TITLE = re.compile(
    r"^(parties|party|effective date|purpose|background|recitals?|preamble|"
    r"definitions?|interpretation|notices?|notice provisions?|entire agreement|"
    r"counterparts?|signatures?|execution|general provisions?|"
    r"boilerplate|severability|headings?|amendments?|waivers?|"
    r"assignment|electronic signatures?|agreed and accepted|in witness whereof|"
    r"relationship of (the )?parties)\b",
    re.IGNORECASE,
)


def normalize_section_title(title: str) -> str:
    """Strip leading section numbers (e.g. '10.5 Notices' -> 'Notices')."""
    t = (title or "").strip()
    normalized = _SECTION_PREFIX.sub("", t, count=1).strip()
    return normalized or t


def is_boilerplate_section(section: IndexedChunk) -> bool:
    """Title-level boilerplate (parties, purpose, definitions, notices, counterparts)."""
    raw = (section.title or section.section_id or "").strip()
    if not raw:
        return False
    title = normalize_section_title(raw)
    if _GOVERNING_LAW_TITLE.search(title):
        return False
    return bool(_BOILERPLATE_TITLE.search(title))


def is_non_substantive_section(section: IndexedChunk) -> bool:
    """Alias for classify/compare skip gate (Phase 22 P8)."""
    return is_boilerplate_section(section)


def _rationale_suffix(
    *,
    gap_type: str,
    section: IndexedChunk | None,
    categories: list[str] | None,
) -> str:
    cats = ", ".join(categories or []) or "general"
    title = (section.title if section else None) or "this section"
    if gap_type == "no_policy":
        return (
            f" No {cats} playbook in discovered scope; {title} marked inconclusive "
            "pending playbook alignment."
        )
    if gap_type == "compare_omitted":
        return " Pending re-compare against retrieved playbook sections."
    if gap_type == "coverage_backfill":
        return f" Section {title} had no finding after compare and final verify."
    if gap_type == "compare_failed":
        return ""
    return ""


def resolve_gap_finding_status(
    section: IndexedChunk | None,
    *,
    gap_type: str,
    categories: list[str] | None = None,
    settings: ReviewSettings | None = None,
) -> tuple[ComplianceStatus, ReviewOutcome, str]:
    """Return (status, review_outcome, rationale_suffix)."""
    cfg = settings or get_settings()
    suffix = _rationale_suffix(gap_type=gap_type, section=section, categories=categories)

    if not cfg.gap_status_substantive_inconclusive:
        legacy_outcome: ReviewOutcome = "compare_failed" if gap_type == "compare_failed" else "playbook_gap"
        if gap_type == "compare_failed":
            return ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT, legacy_outcome, suffix
        return ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT, legacy_outcome, suffix

    if gap_type == "compare_failed":
        return ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT, "compare_failed", suffix

    boilerplate = section is not None and is_boilerplate_section(section)

    if gap_type == "no_policy":
        if boilerplate:
            return (
                ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
                "boilerplate",
                " Standard boilerplate provision; no playbook coverage required.",
            )
        return ComplianceStatus.INCONCLUSIVE, "playbook_gap", suffix

    if gap_type == "compare_omitted":
        return ComplianceStatus.INCONCLUSIVE, "pipeline_incomplete", suffix

    if gap_type == "coverage_backfill":
        if boilerplate:
            return (
                ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
                "boilerplate",
                suffix,
            )
        return ComplianceStatus.INCONCLUSIVE, "pipeline_incomplete", suffix

    return ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT, "playbook_gap", suffix


def upgrade_substantive_gap_finding(
    finding: ComplianceFinding,
    section: IndexedChunk | None,
    *,
    settings: ReviewSettings | None = None,
) -> ComplianceFinding:
    """Upgrade gap LLM INSUFFICIENT on substantive sections to INCONCLUSIVE."""
    cfg = settings or get_settings()
    if not cfg.gap_upgrade_after_gap_llm or not cfg.gap_status_substantive_inconclusive:
        return finding
    if finding.status != ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT:
        return finding
    if section is not None and is_boilerplate_section(section):
        meta = dict(finding.metadata or {})
        meta["review_outcome"] = "boilerplate"
        return finding.model_copy(update={"metadata": meta})

    meta = dict(finding.metadata or {})
    meta["review_outcome"] = "playbook_gap"
    meta["status_upgraded_from"] = ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT.value
    rationale = (finding.rationale or "").strip()
    if "pending playbook alignment" not in rationale.lower():
        rationale = f"{rationale} Marked inconclusive pending playbook alignment.".strip()
    return finding.model_copy(
        update={
            "status": ComplianceStatus.INCONCLUSIVE,
            "metadata": meta,
            "rationale": rationale,
        }
    )


def gap_status_summary(findings: list[ComplianceFinding]) -> dict[str, int]:
    """Ops counts from final findings metadata."""
    summary = {
        "insufficient_boilerplate": 0,
        "inconclusive_playbook_gap": 0,
        "pipeline_incomplete": 0,
        "coverage_backfill": 0,
    }
    for finding in findings:
        meta = finding.metadata or {}
        outcome = meta.get("review_outcome")
        if outcome == "boilerplate":
            summary["insufficient_boilerplate"] += 1
        elif outcome == "playbook_gap":
            summary["inconclusive_playbook_gap"] += 1
        elif outcome == "pipeline_incomplete":
            summary["pipeline_incomplete"] += 1
        if meta.get("gap_type") == "coverage_backfill":
            summary["coverage_backfill"] += 1
    return summary
