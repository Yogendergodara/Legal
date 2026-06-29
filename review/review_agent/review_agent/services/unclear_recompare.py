"""Rules for final-verify unclear re-compare eligibility (Phase 21 P0-B)."""

from __future__ import annotations

from typing import Literal

from document_core.schemas.compliance import ComplianceFinding, ComplianceStatus

UnclearReason = Literal[
    "low_confidence",
    "playbook_inconclusive",
    "compare_failed",
    "rate_limited",
    "contract_silent",
    "gap_context",
    "coverage_gate_ipc",
    "obligation_evidence_ipc",
    "inconclusive_other",
]

_LOW_CONFIDENCE_MAX = 0.5
_PLAYBOOK_INCONCLUSIVE_MAX = 0.75

_RECOMPARE_REASONS = frozenset(
    {
        "low_confidence",
        "playbook_inconclusive",
        "compare_failed",
        "rate_limited",
        "coverage_gate_ipc",
        "obligation_evidence_ipc",
    }
)

_SILENT_MARKERS = (
    "does not mention",
    "does not reference",
    "not explicitly",
    "no explicit",
    "contract silent",
    "too general",
    "too vague",
    "does not address",
    "no direct reference",
)


def _has_policy_context(finding: ComplianceFinding) -> bool:
    meta = finding.metadata or {}
    return bool(
        finding.contract_section_id
        and (
            finding.policy_quote
            or finding.policy_document_id
            or meta.get("policy_document_id")
        )
    )


def _obligation_evidence_ipc_reason(meta: dict) -> str | None:
    if str(meta.get("source") or "") != "obligation_ipc":
        return None
    audit = meta.get("routing_audit")
    if not isinstance(audit, dict):
        return None
    evidence = audit.get("evidence")
    if not isinstance(evidence, dict):
        return None
    if evidence.get("decision") != "ipc":
        return None
    reason = str(evidence.get("reason") or "").strip()
    if not reason or reason in ("routing_or_skip", "ipc_preflight"):
        return None
    return reason


def classify_unclear_finding(finding: ComplianceFinding) -> UnclearReason:
    meta = finding.metadata or {}
    gap_type = str(meta.get("gap_type") or "")
    rationale = (finding.rationale or "").lower()
    source = str(meta.get("source") or "")

    if gap_type in ("no_policy", "compare_omitted"):
        return "gap_context"

    if gap_type == "coverage_gate_ipc":
        return "coverage_gate_ipc"

    if _obligation_evidence_ipc_reason(meta) and _has_policy_context(finding):
        return "obligation_evidence_ipc"

    if rationale.startswith("section compare failed:"):
        if "429" in rationale or "rate limit" in rationale or "rate_limited" in rationale:
            return "rate_limited"
        return "compare_failed"

    if source == "section_compare_failed":
        return "compare_failed"

    if finding.status == ComplianceStatus.INCONCLUSIVE and any(m in rationale for m in _SILENT_MARKERS):
        return "contract_silent"

    confidence = meta.get("confidence")
    if (
        source == "playbook_compare"
        and confidence is not None
        and finding.status == ComplianceStatus.INCONCLUSIVE
        and _LOW_CONFIDENCE_MAX <= float(confidence) < _PLAYBOOK_INCONCLUSIVE_MAX
        and _has_policy_context(finding)
        and gap_type not in ("no_policy", "compare_omitted")
    ):
        return "playbook_inconclusive"

    if (
        source == "playbook_compare"
        and confidence is not None
        and float(confidence) < _LOW_CONFIDENCE_MAX
        and _has_policy_context(finding)
    ):
        return "low_confidence"

    return "inconclusive_other"


def eligible_for_unclear_recompare(finding: ComplianceFinding) -> bool:
    reason = classify_unclear_finding(finding)
    if reason == "coverage_gate_ipc":
        from review_agent.config import get_settings

        if not get_settings().final_verify_coverage_gate_recompare_enabled:
            return False
        return bool(finding.contract_section_id)
    if reason == "obligation_evidence_ipc":
        return _has_policy_context(finding)
    if reason not in _RECOMPARE_REASONS:
        return False
    if reason in ("compare_failed", "rate_limited"):
        source = str((finding.metadata or {}).get("source") or "")
        if source not in ("playbook_compare", "section_compare_failed"):
            return False
        return _has_policy_context(finding)
    return True


def section_has_grounded_non_compliant(
    section_id: str,
    findings: list[ComplianceFinding],
) -> bool:
    """Do not re-compare sections that already have a grounded violation."""
    for finding in findings:
        if finding.contract_section_id != section_id:
            continue
        if finding.status != ComplianceStatus.NON_COMPLIANT:
            continue
        if (finding.metadata or {}).get("source") != "playbook_compare":
            continue
        if finding.grounded is True:
            return True
        if (finding.contract_quote or "").strip() and (finding.policy_quote or "").strip():
            return True
    return False
