"""Tests for finding metadata enrichment (Phase 6B)."""

from __future__ import annotations

from uuid import uuid4

from document_core.schemas.compliance import ComplianceFinding, ComplianceStatus, Severity

from review_agent.services.finding_enrich import (
    build_policy_title_map,
    enrich_findings_policy_titles,
)


def test_build_policy_title_map_merges_indexed_and_discovered():
    doc_id = str(uuid4())
    title_map = build_policy_title_map(
        [{"document_id": doc_id, "title": "Vendor Playbook"}],
        [{"document_id": str(uuid4()), "title": "NDA Policy"}],
    )
    assert title_map[doc_id] == "Vendor Playbook"
    assert len(title_map) == 2


def test_enrich_findings_policy_titles_sets_metadata():
    doc_id = uuid4()
    finding = ComplianceFinding(
        finding_id="f1",
        dimension_id="d1",
        dimension_label="Limitation of Liability",
        status=ComplianceStatus.NON_COMPLIANT,
        severity=Severity.CRITICAL,
        policy_document_id=doc_id,
        rationale="Gap",
    )
    enriched = enrich_findings_policy_titles(
        [finding],
        {str(doc_id): "Vendor Playbook 2024"},
    )
    assert enriched[0].metadata["policy_title"] == "Vendor Playbook 2024"


def test_enrich_skips_when_title_already_set():
    doc_id = uuid4()
    finding = ComplianceFinding(
        finding_id="f1",
        dimension_id="d1",
        dimension_label="Liability",
        status=ComplianceStatus.NON_COMPLIANT,
        severity=Severity.IMPORTANT,
        policy_document_id=doc_id,
        metadata={"policy_title": "Existing Title"},
    )
    enriched = enrich_findings_policy_titles(
        [finding],
        {str(doc_id): "Other Title"},
    )
    assert enriched[0].metadata["policy_title"] == "Existing Title"
