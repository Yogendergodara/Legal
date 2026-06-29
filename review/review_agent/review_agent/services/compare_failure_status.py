"""Shared compare failure status classification (CA-1)."""

from __future__ import annotations

from document_core.schemas.compliance import ComplianceStatus

from review_agent.resilience.failure_policy import FailureClass, ReviewPosture, classify_llm_failure

_TRANSIENT_MARKERS = ("429", "rate limit", "rate_limited", "timeout", "validation error")


def classify_compare_failure(
    reason: str,
    *,
    has_policy_evidence: bool,
    transient_inconclusive: bool = True,
    obligation_section_cutover_mode: str = "skip",
    llm_review_posture: str = "normal",
) -> ComplianceStatus:
    failure_class = classify_llm_failure(reason)
    try:
        posture = ReviewPosture(llm_review_posture)
    except ValueError:
        posture = ReviewPosture.NORMAL

    if posture in (ReviewPosture.HOT, ReviewPosture.DEGRADED) and failure_class == FailureClass.QUOTA:
        if has_policy_evidence:
            return ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT

    if obligation_section_cutover_mode == "ipc_fallback" and has_policy_evidence:
        if not transient_inconclusive:
            return ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT
        text = (reason or "").lower()
        if any(marker in text for marker in _TRANSIENT_MARKERS):
            return ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT
    if not transient_inconclusive:
        return ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT
    text = (reason or "").lower()
    if any(marker in text for marker in _TRANSIENT_MARKERS):
        return ComplianceStatus.INCONCLUSIVE
    if not has_policy_evidence:
        return ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT
    return ComplianceStatus.INCONCLUSIVE
