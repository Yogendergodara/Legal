"""Tests for unclear re-compare eligibility rules."""

from uuid import uuid4

from document_core.schemas.compliance import ComplianceFinding, ComplianceStatus, Severity

from review_agent.services.unclear_recompare import (
    classify_unclear_finding,
    eligible_for_unclear_recompare,
    section_has_grounded_non_compliant,
)


def _finding(**kwargs) -> ComplianceFinding:
    defaults = {
        "finding_id": "f1",
        "dimension_id": "s1:5",
        "dimension_label": "Indemnification",
        "status": ComplianceStatus.INCONCLUSIVE,
        "contract_section_id": "s1",
        "rationale": "Uncertain alignment.",
        "metadata": {},
    }
    defaults.update(kwargs)
    return ComplianceFinding(**defaults)


def test_classify_low_confidence_playbook_compare():
    finding = _finding(
        metadata={"source": "playbook_compare", "confidence": 0.3},
        policy_quote="Vendor must indemnify.",
    )
    assert classify_unclear_finding(finding) == "low_confidence"
    assert eligible_for_unclear_recompare(finding)


def test_classify_playbook_inconclusive_moderate_confidence():
    finding = _finding(
        metadata={"source": "playbook_compare", "confidence": 0.6},
        policy_quote="Topics not listed are outside scope.",
        rationale="Contract reference to policies is too vague to confirm compliance.",
    )
    assert classify_unclear_finding(finding) == "contract_silent"
    assert not eligible_for_unclear_recompare(finding)


def test_playbook_inconclusive_eligible_without_silent_markers():
    finding = _finding(
        metadata={"source": "playbook_compare", "confidence": 0.6},
        policy_quote="Kick-off meeting within 30 days.",
        rationale="Contract does not specify kick-off timeline while policy requires 30 days.",
    )
    assert classify_unclear_finding(finding) == "playbook_inconclusive"
    assert eligible_for_unclear_recompare(finding)


def test_ipc_high_confidence_not_eligible():
    finding = _finding(
        status=ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
        metadata={"source": "playbook_compare", "confidence": 1.0},
        policy_quote="AI usage policy section.",
        rationale="Retrieved policy does not cover third-party products.",
    )
    assert classify_unclear_finding(finding) == "inconclusive_other"
    assert not eligible_for_unclear_recompare(finding)


def test_classify_compare_failed():
    finding = _finding(
        rationale="Section compare failed: timeout",
        metadata={"source": "section_compare_failed", "gap_type": "compare_failed"},
        policy_quote="Vendor must indemnify.",
    )
    assert classify_unclear_finding(finding) == "compare_failed"
    assert eligible_for_unclear_recompare(finding)


def test_compare_failed_requires_policy_context():
    finding = _finding(
        rationale="Section compare failed: timeout",
        metadata={"source": "section_compare_failed", "gap_type": "compare_failed"},
    )
    assert classify_unclear_finding(finding) == "compare_failed"
    assert not eligible_for_unclear_recompare(finding)


def test_classify_rate_limited():
    finding = _finding(
        rationale="Section compare failed: 429 rate limit exceeded",
        metadata={"source": "section_compare_failed", "gap_type": "compare_failed"},
        policy_quote="Vendor must indemnify.",
    )
    assert classify_unclear_finding(finding) == "rate_limited"
    assert eligible_for_unclear_recompare(finding)


def test_classify_gap_context():
    finding = _finding(
        status=ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
        metadata={"gap_type": "no_policy"},
    )
    assert classify_unclear_finding(finding) == "gap_context"
    assert not eligible_for_unclear_recompare(finding)


def test_classify_contract_silent():
    finding = _finding(
        status=ComplianceStatus.INCONCLUSIVE,
        rationale="Contract does not mention indemnification obligations.",
    )
    assert classify_unclear_finding(finding) == "contract_silent"
    assert not eligible_for_unclear_recompare(finding)


def test_low_confidence_requires_policy_context():
    finding = _finding(
        metadata={"source": "playbook_compare", "confidence": 0.2},
    )
    assert classify_unclear_finding(finding) == "inconclusive_other"
    assert not eligible_for_unclear_recompare(finding)


def test_obligation_evidence_ipc_eligible():
    finding = _finding(
        status=ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
        metadata={
            "source": "obligation_ipc",
            "routing_audit": {
                "evidence": {"decision": "ipc", "reason": "low_concept_overlap"},
            },
        },
        policy_quote="Privacy retention limits.",
    )
    assert classify_unclear_finding(finding) == "obligation_evidence_ipc"
    assert eligible_for_unclear_recompare(finding)


def test_routing_or_skip_obligation_ipc_not_eligible():
    finding = _finding(
        status=ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
        metadata={
            "source": "obligation_ipc",
            "routing_audit": {
                "evidence": {"decision": "ipc", "reason": "routing_or_skip"},
            },
        },
    )
    assert classify_unclear_finding(finding) == "inconclusive_other"
    assert not eligible_for_unclear_recompare(finding)


def test_section_has_grounded_non_compliant():
    policy_doc = uuid4()
    grounded = _finding(
        finding_id="nc1",
        status=ComplianceStatus.NON_COMPLIANT,
        contract_quote="Limited liability cap.",
        policy_quote="Unlimited liability required.",
        metadata={"source": "playbook_compare"},
        grounded=True,
    )
    assert section_has_grounded_non_compliant("s1", [grounded])

    ungrounded = _finding(
        finding_id="nc2",
        status=ComplianceStatus.NON_COMPLIANT,
        metadata={"source": "playbook_compare"},
    )
    assert not section_has_grounded_non_compliant("s1", [ungrounded])
