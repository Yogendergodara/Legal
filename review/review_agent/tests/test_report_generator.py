"""Tests for markdown report rendering."""

from __future__ import annotations

from uuid import uuid4

from document_core.schemas.compliance import ComplianceFinding, ComplianceStatus, ReviewReport, Severity

from review_agent.reports.generator import render_markdown_report


def test_report_shows_violated_policy_line():
    doc_id = uuid4()
    finding = ComplianceFinding(
        finding_id="f1",
        dimension_id="d1",
        dimension_label="Limitation of Liability",
        status=ComplianceStatus.NON_COMPLIANT,
        severity=Severity.CRITICAL,
        policy_document_id=doc_id,
        contract_quote="unlimited liability",
        policy_quote="liability shall not exceed fees",
        rationale="Contract removes cap.",
        grounded=True,
        metadata={"policy_title": "Vendor Playbook 2024"},
    )
    report = ReviewReport(
        tenant_id="demo",
        contract_document_id=uuid4(),
        contract_title="MSA",
        findings=[finding],
    )
    md = render_markdown_report(report)
    assert "Violated policy" in md
    assert "Vendor Playbook 2024" in md
    assert "Limitation of Liability" in md
