"""Tests for hybrid finding merge."""

from __future__ import annotations

from document_core.schemas.compliance import ComplianceFinding, ComplianceStatus
from review_agent.services.compliance_merge import merge_compliance_findings


def _finding(dimension_id: str, status: ComplianceStatus) -> ComplianceFinding:
    return ComplianceFinding(
        finding_id="f1",
        dimension_id=dimension_id,
        dimension_label=dimension_id,
        status=status,
        rationale="test rationale for merge",
    )


def test_merge_pass2_overrides_pass1():
    merged = merge_compliance_findings(
        prescreen=[_finding("a", ComplianceStatus.COMPLIANT)],
        pass1=[_finding("a", ComplianceStatus.INCONCLUSIVE)],
        pass2=[_finding("a", ComplianceStatus.NON_COMPLIANT)],
    )
    assert len(merged) == 1
    assert merged[0].status == ComplianceStatus.NON_COMPLIANT


def test_merge_preserves_distinct_categories():
    merged = merge_compliance_findings(
        prescreen=[_finding("a", ComplianceStatus.COMPLIANT)],
        pass1=[_finding("b", ComplianceStatus.INCONCLUSIVE)],
        pass2=[],
    )
    assert {f.dimension_id for f in merged} == {"a", "b"}
