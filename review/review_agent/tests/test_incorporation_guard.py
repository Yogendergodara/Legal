"""Tests for incorporation-by-reference guard (Phase C1)."""

from __future__ import annotations

from uuid import UUID

from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk
from document_core.schemas.compliance import ComplianceStatus, Severity
from review_agent.schemas.section_compare import SectionCompareItem
from review_agent.services.incorporation_guard import apply_incorporation_guard


def _section(section_id: str, text: str) -> IndexedChunk:
    return IndexedChunk(
        chunk_id=f"c-{section_id}",
        document_id=UUID("00000000-0000-0000-0000-000000000001"),
        tenant_id="demo",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id=section_id,
        section_path=section_id,
        title="Conduct",
        text=text,
    )


def test_upgrades_false_code_of_conduct_acknowledgment_gap() -> None:
    text = (
        "The Receiving Party agrees to uphold Xecurify's Code of Conduct principles "
        "during the term of this Agreement."
    )
    item = SectionCompareItem(
        section_id="5.1",
        dimension_label="Explicit Acknowledgment of Code of Conduct",
        status=ComplianceStatus.NON_COMPLIANT,
        severity=Severity.IMPORTANT,
        contract_quote="agrees to uphold Xecurify's Code of Conduct principles",
        policy_quote="All personnel must comply with the Code of Conduct.",
        rationale="Contract does not explicitly acknowledge the full scope of the Code of Conduct.",
    )
    upgraded, count = apply_incorporation_guard(
        [item],
        {"5.1": _section("5.1", text)},
    )
    assert count == 1
    assert upgraded[0].status == ComplianceStatus.COMPLIANT
    assert upgraded[0].severity == Severity.INFO


def test_keeps_real_material_contradiction() -> None:
    text = (
        "Receiving Party agrees to uphold Xecurify's Code of Conduct but may subcontract "
        "without any conduct obligations."
    )
    item = SectionCompareItem(
        section_id="5.1",
        dimension_label="Subcontractor Conduct",
        status=ComplianceStatus.NON_COMPLIANT,
        severity=Severity.CRITICAL,
        contract_quote="may subcontract without any conduct obligations",
        policy_quote="Subcontractors must comply with the Code of Conduct.",
        rationale="Contract materially deviates by allowing subcontractors without conduct obligations.",
    )
    kept, count = apply_incorporation_guard(
        [item],
        {"5.1": _section("5.1", text)},
    )
    assert count == 0
    assert kept[0].status == ComplianceStatus.NON_COMPLIANT


def test_skips_when_no_named_policy_reference() -> None:
    item = SectionCompareItem(
        section_id="2.1",
        dimension_label="Confidentiality",
        status=ComplianceStatus.NON_COMPLIANT,
        severity=Severity.IMPORTANT,
        contract_quote="shall keep information confidential",
        policy_quote="Information must be protected.",
        rationale="Contract is silent on secure deletion requirements.",
    )
    kept, count = apply_incorporation_guard(
        [item],
        {"2.1": _section("2.1", "shall keep information confidential")},
    )
    assert count == 0
    assert kept[0].status == ComplianceStatus.NON_COMPLIANT
