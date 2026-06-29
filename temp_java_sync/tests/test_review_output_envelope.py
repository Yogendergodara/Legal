"""Tests for canonical review output envelope (P3-10)."""

from __future__ import annotations

from uuid import uuid4

from document_core.schemas.compliance import (
    ComplianceFinding,
    ComplianceStatus,
    ReviewReport,
    Severity,
)
from review_output import (
    REVIEW_OUTPUT_SCHEMA_VERSION,
    build_review_output_envelope,
    parse_findings_from_envelope,
)


def _finding() -> ComplianceFinding:
    return ComplianceFinding(
        finding_id="f1",
        dimension_id="s1:cap",
        dimension_label="Liability Cap",
        status=ComplianceStatus.NON_COMPLIANT,
        severity=Severity.CRITICAL,
        contract_quote="quote",
        rationale="test",
        contract_section_id="s1",
    )


def _report() -> ReviewReport:
    finding = _finding()
    return ReviewReport(
        tenant_id="demo",
        contract_document_id=uuid4(),
        contract_title="NDA",
        findings=[finding],
        summary_markdown="# Summary",
        metadata={"pipeline": "section_first", "artifact": {"artifact_version": "1.0"}},
    )


def test_envelope_root_findings():
    report = _report()
    state = {"warnings": ["w1"], "discovered_policy_document_ids": ["p1"]}
    data = build_review_output_envelope(
        report=report,
        state=state,
        contract_document_id="doc-1",
    )
    assert data["schema_version"] == REVIEW_OUTPUT_SCHEMA_VERSION
    assert data["finding_count"] == 1
    assert len(data["findings"]) == 1
    assert data["findings"][0]["finding_id"] == "f1"
    assert data["artifacts"]["report"]["findings"]


def test_parse_legacy_e2e_flat():
    legacy = {"findings": [{"finding_id": "f1", "status": "NON_COMPLIANT"}]}
    parsed = parse_findings_from_envelope(legacy)
    assert len(parsed) == 1
    assert parsed[0]["finding_id"] == "f1"


def test_parse_legacy_dev_ui_nested():
    legacy = {
        "artifacts": {
            "report": {
                "findings": [{"finding_id": "f2", "status": "COMPLIANT"}],
            }
        }
    }
    parsed = parse_findings_from_envelope(legacy)
    assert len(parsed) == 1
    assert parsed[0]["finding_id"] == "f2"


def test_parse_legacy_empty():
    assert parse_findings_from_envelope({}) == []


def test_finding_count_consistent():
    report = _report()
    data = build_review_output_envelope(report=report, state={})
    assert data["finding_count"] == len(data["findings"])


def test_envelope_engine_diagnosis_mirror():
    report = _report()
    diagnosis = {
        "schema_version": "1.0",
        "pipeline_mode": "section_first",
        "ipc_summary": {"section_ipc_pct": 0.0},
    }
    report.metadata["engine_diagnosis"] = diagnosis
    report.metadata["artifact"] = {
        **report.metadata.get("artifact", {}),
        "engine_diagnosis": diagnosis,
    }
    data = build_review_output_envelope(report=report, state={})
    assert data["engine_diagnosis"] == diagnosis
    assert data["artifacts"]["report"]["metadata"]["engine_diagnosis"] == diagnosis
    assert data["artifact"]["engine_diagnosis"] == diagnosis
