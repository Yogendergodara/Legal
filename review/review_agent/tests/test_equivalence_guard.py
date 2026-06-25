"""Tests for semantic equivalence guard (Phase C4)."""

from __future__ import annotations

from document_core.schemas.compliance import ComplianceStatus, Severity
from review_agent.schemas.section_compare import SectionCompareItem
from review_agent.services.equivalence_guard import apply_equivalence_guard


def test_downgrades_data_principal_rights_legal_retention() -> None:
    item = SectionCompareItem(
        section_id="4.2",
        dimension_label="Data Principal Rights",
        status=ComplianceStatus.NON_COMPLIANT,
        severity=Severity.CRITICAL,
        contract_quote="subject to applicable legal retention requirements",
        policy_quote="except where retention is otherwise required by law",
        rationale=(
            "Contract references legal retention requirements but policy explicitly "
            "requires exceptions where retention is required by law."
        ),
    )
    result, count = apply_equivalence_guard([item])
    assert count == 1
    assert result[0].status == ComplianceStatus.COMPLIANT
    assert result[0].severity == Severity.INFO


def test_keeps_prohibition_gap() -> None:
    item = SectionCompareItem(
        section_id="4.2",
        dimension_label="Data Principal Rights",
        status=ComplianceStatus.NON_COMPLIANT,
        severity=Severity.CRITICAL,
        contract_quote="Data Principal shall not have any right to erasure",
        policy_quote="Data principals may request erasure subject to legal retention.",
        rationale="Contract prohibits erasure rights that policy requires.",
    )
    result, count = apply_equivalence_guard([item])
    assert count == 0
    assert result[0].status == ComplianceStatus.NON_COMPLIANT
    assert result[0].severity == Severity.CRITICAL
