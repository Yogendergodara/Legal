"""Tests for markdown report synthesizer (P5.3/P5.4)."""

from __future__ import annotations

import uuid

from document_core.schemas.compliance import ComplianceFinding, ComplianceStatus, ReviewReport, Severity
from review_agent.reports.generator import render_markdown_report
from review_agent.schemas.review_artifact import ReviewArtifact, ReviewArtifactOps, SectionAuditRow


def test_render_markdown_includes_summary_and_ops():
    artifact = ReviewArtifact(
        run_id="run-1",
        tenant_id="demo",
        contract_document_id=str(uuid.uuid4()),
        contract_title="MSA",
        sections=[SectionAuditRow(section_id="s1", title="Liability")],
        discovery={"discovered_policy_document_ids": ["p1", "p2"]},
        compliance_stats={
            "review_confidence": {"downgrade_quote_validate": 2},
        },
        ops=ReviewArtifactOps(
            retrieval_retry_sections=3,
            backfill_count=1,
            ungrounded_count=2,
            playbook_compare_count=4,
            policy_conflict_count=1,
        ),
    )
    report = ReviewReport(
        tenant_id="demo",
        contract_document_id=uuid.uuid4(),
        contract_title="MSA",
        findings=[
            ComplianceFinding(
                finding_id="f1",
                dimension_id="s1:x",
                dimension_label="Liability Cap",
                status=ComplianceStatus.NON_COMPLIANT,
                severity=Severity.CRITICAL,
                contract_section_id="s1",
                rationale="Cap too low.",
            )
        ],
    )
    md = render_markdown_report(report, artifact=artifact)
    assert "## Executive summary" in md
    assert "downgraded at compare quote validate" in md
    assert "**2** finding(s) downgraded at compare quote validate" in md
    assert "## Pipeline operations" in md
    assert "Retrieval retries (sections) | 3" in md
    assert "Playbook compare findings | 4" in md
    assert "## Findings" in md
    assert "Liability Cap" in md
    assert "compare_items" not in md.lower()


def test_render_markdown_renders_degraded_count():
    artifact = ReviewArtifact(
        run_id="run-2",
        tenant_id="demo",
        contract_document_id=str(uuid.uuid4()),
        contract_title="NDA",
        ops=ReviewArtifactOps(
            retrieval_zero_hit_sections=2,
            degraded_section_count=2,
            retrieval_zero_hit_section_ids=["6", "7"],
        ),
    )
    report = ReviewReport(
        tenant_id="demo",
        contract_document_id=uuid.uuid4(),
        contract_title="NDA",
        findings=[],
    )
    md = render_markdown_report(report, artifact=artifact)
    assert "Degraded sections | 2" in md
    assert "Zero-hit section IDs | 6, 7" in md


def test_render_markdown_without_artifact_still_works():
    report = ReviewReport(
        tenant_id="demo",
        contract_document_id=uuid.uuid4(),
        contract_title="NDA",
        findings=[],
        metadata={"reviewable_section_count": 5, "discovered_policy_document_ids": ["a"]},
    )
    md = render_markdown_report(report)
    assert "## Executive summary" in md
    assert "## Findings" in md
    assert "## Pipeline operations" not in md
