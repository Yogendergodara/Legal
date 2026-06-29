"""Tests for topic-mismatch post-compare guard (Phase I)."""

from __future__ import annotations

from uuid import UUID, uuid4

from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk, RetrievalHit
from document_core.schemas.compliance import ComplianceStatus, Severity
from review_agent.schemas.section_compare import SectionCompareItem
from review_agent.services.topic_mismatch_guard import apply_topic_mismatch_guard

IR_DOC = UUID("00000000-0000-0000-0000-000000000010")
LIAB_DOC = UUID("00000000-0000-0000-0000-000000000011")


def _contract_section(
    section_id: str,
    title: str,
    text: str,
) -> IndexedChunk:
    return IndexedChunk(
        chunk_id=f"c-{section_id}",
        document_id=uuid4(),
        tenant_id="demo",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id=section_id,
        section_path=section_id,
        title=title,
        text=text,
    )


def _policy_hit(
    *,
    doc_id: UUID,
    section_id: str,
    title: str,
    categories: list[str],
) -> RetrievalHit:
    chunk = IndexedChunk(
        chunk_id=f"{doc_id}:{section_id}",
        document_id=doc_id,
        tenant_id="demo",
        kind=DocumentKind.POLICY,
        chunk_role=ChunkRole.PARENT,
        section_id=section_id,
        section_path=section_id,
        title=title,
        text=f"{title} policy body",
        metadata={"categories": categories},
    )
    return RetrievalHit(parent_chunk=chunk, matched_child_ids=[], score=0.9)


def test_governing_law_incident_nc_downgraded_to_ipc() -> None:
    section = _contract_section(
        "10.1",
        "Governing Law",
        "This Agreement shall be governed by the laws of Wyoming.",
    )
    hit = _policy_hit(
        doc_id=IR_DOC,
        section_id="10",
        title="Incident Response Plan",
        categories=["incident_reporting"],
    )
    item = SectionCompareItem(
        section_id="10.1",
        policy_document_id=str(IR_DOC),
        policy_section_id="10",
        dimension_label="Incident Reporting Requirement",
        status=ComplianceStatus.NON_COMPLIANT,
        severity=Severity.IMPORTANT,
        contract_quote="governed by the laws of Wyoming",
        policy_quote="ISMS Team prepares incident report",
        rationale="Contract does not address incident reporting requirements.",
    )
    result, count = apply_topic_mismatch_guard(
        [item],
        sections_by_id={"10.1": section},
        categories_by_section={"10.1": ["governing_law"]},
        hits_by_section={"10.1": [hit]},
    )
    assert count == 1
    assert result[0].status == ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT
    assert result[0].severity == Severity.INFO
    assert "Topic mismatch" in result[0].rationale


def test_notices_incident_critical_nc_downgraded_to_ipc() -> None:
    section = _contract_section(
        "10.5",
        "10.5 Notices",
        "All notices shall be in writing and delivered to the addresses herein.",
    )
    hit = _policy_hit(
        doc_id=IR_DOC,
        section_id="5",
        title="Customer Notification",
        categories=["incident_reporting"],
    )
    item = SectionCompareItem(
        section_id="10.5",
        policy_document_id=str(IR_DOC),
        policy_section_id="5",
        dimension_label="Notice Period for Incidents",
        status=ComplianceStatus.NON_COMPLIANT,
        severity=Severity.CRITICAL,
        contract_quote="All notices shall be in writing",
        policy_quote="notified within 8 hrs of the incident",
        rationale="Contract is silent on 8-hour incident notification.",
    )
    result, count = apply_topic_mismatch_guard(
        [item],
        sections_by_id={"10.5": section},
        categories_by_section={"10.5": ["general"]},
        hits_by_section={"10.5": [hit]},
    )
    assert count == 1
    assert result[0].status == ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT


def test_liability_gap_unchanged_when_categories_align() -> None:
    section = _contract_section(
        "9",
        "Limitation of Liability",
        "Liability shall not exceed fees paid in twelve months.",
    )
    hit = _policy_hit(
        doc_id=LIAB_DOC,
        section_id="1",
        title="Liability Cap",
        categories=["liability"],
    )
    item = SectionCompareItem(
        section_id="9",
        policy_document_id=str(LIAB_DOC),
        policy_section_id="1",
        dimension_label="Liability Cap Amount",
        status=ComplianceStatus.NON_COMPLIANT,
        severity=Severity.CRITICAL,
        contract_quote="twelve months",
        policy_quote="no less than twenty-four months",
        rationale="Cap is below policy minimum.",
    )
    result, count = apply_topic_mismatch_guard(
        [item],
        sections_by_id={"9": section},
        categories_by_section={"9": ["liability"]},
        hits_by_section={"9": [hit]},
    )
    assert count == 0
    assert result[0].status == ComplianceStatus.NON_COMPLIANT


def test_governing_law_incident_downgraded_via_catalog_when_chunk_untagged() -> None:
    section = _contract_section(
        "10.1",
        "Governing Law",
        "This Agreement shall be governed by the laws of Wyoming.",
    )
    hit = _policy_hit(
        doc_id=IR_DOC,
        section_id="10",
        title="Incident Response Plan",
        categories=[],
    )
    item = SectionCompareItem(
        section_id="10.1",
        policy_document_id=str(IR_DOC),
        policy_section_id="10",
        dimension_label="Incident Reporting Requirement",
        status=ComplianceStatus.NON_COMPLIANT,
        severity=Severity.IMPORTANT,
        contract_quote="governed by the laws of Wyoming",
        policy_quote="ISMS Team prepares incident report",
        rationale="Contract does not address incident reporting requirements.",
    )
    catalog = {str(IR_DOC): ["incident_reporting", "records_management"]}
    result, count = apply_topic_mismatch_guard(
        [item],
        sections_by_id={"10.1": section},
        categories_by_section={"10.1": ["governing_law"]},
        hits_by_section={"10.1": [hit]},
        doc_catalog_categories=catalog,
    )
    assert count == 1
    assert result[0].status == ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT
