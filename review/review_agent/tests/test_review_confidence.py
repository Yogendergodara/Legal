"""Tests for review confidence metrics (Phase E3)."""

from __future__ import annotations

from document_core.schemas.compliance import ComplianceFinding, ComplianceStatus, Severity
from review_agent.services.review_confidence import compute_review_confidence_metrics


def _finding(**kwargs) -> ComplianceFinding:
    base = {
        "finding_id": "f1",
        "dimension_id": "s1:test",
        "dimension_label": "Test",
        "status": ComplianceStatus.INCONCLUSIVE,
        "severity": Severity.INFO,
        "contract_section_id": "1",
        "rationale": "Downgraded: model quotes were not exact substrings of the provided sections.",
        "metadata": {},
    }
    base.update(kwargs)
    return ComplianceFinding(**base)


def test_confidence_metrics_counts_downgrades() -> None:
    findings = [
        _finding(contract_section_id="1"),
        _finding(
            contract_section_id="2",
            status=ComplianceStatus.COMPLIANT,
            rationale="Aligned.",
            metadata={"grounding_failed": True},
        ),
        _finding(
            contract_section_id="3",
            status=ComplianceStatus.NON_COMPLIANT,
            severity=Severity.CRITICAL,
            rationale="Gap.",
        ),
    ]
    metrics = compute_review_confidence_metrics(findings, sections_total=3)
    assert metrics["downgrade_quote_validate"] == 1
    assert metrics["downgrade_grounding"] == 1
    assert metrics["confident_section_pct"] == 66.7
