"""Tests for section-first finding merge."""

from document_core.schemas.compliance import ComplianceStatus, Severity
from review_agent.schemas.section_compare import SectionCompareItem
from review_agent.schemas.section_retrieval import SectionRetrievalBundle
from review_agent.services.section_merge import merge_section_findings


def test_merge_dedupes_compare_items():
    items = [
        SectionCompareItem(
            section_id="s1",
            policy_document_id="550e8400-e29b-41d4-a716-446655440000",
            dimension_label="Liability",
            status=ComplianceStatus.NON_COMPLIANT,
            severity=Severity.CRITICAL,
            rationale="Cap missing from contract section.",
        ),
        SectionCompareItem(
            section_id="s1",
            policy_document_id="550e8400-e29b-41d4-a716-446655440000",
            dimension_label="Liability",
            status=ComplianceStatus.NON_COMPLIANT,
            severity=Severity.CRITICAL,
            rationale="Duplicate should be dropped.",
        ),
    ]
    bundles = {
        "s1": SectionRetrievalBundle(section_id="s1", categories=["liability"], policy_hits=[]),
    }
    findings, warnings = merge_section_findings(items, bundles)
    assert len(findings) == 1
    assert findings[0].status == ComplianceStatus.NON_COMPLIANT
    assert not warnings


def test_merge_adds_no_policy_gap():
    bundles = {
        "s2": SectionRetrievalBundle(section_id="s2", categories=["privacy"], policy_hits=[]),
    }
    findings, warnings = merge_section_findings([], bundles)
    assert len(findings) == 1
    assert findings[0].status == ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT
    assert warnings
