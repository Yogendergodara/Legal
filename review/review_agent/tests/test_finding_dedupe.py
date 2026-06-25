"""Tests for compare-item dedupe and per-section cap (Phase 21 P1-D)."""

from __future__ import annotations

from document_core.schemas.compliance import ComplianceStatus, Severity
from review_agent.schemas.section_compare import SectionCompareItem
from review_agent.services.finding_dedupe import (
    cap_compare_items_by_section,
    dedupe_compare_items,
    is_gap_compare_item,
    suppress_contradicted_non_compliant,
)


def _item(
    *,
    section_id: str = "s1",
    policy_document_id: str = "doc-a",
    dimension_label: str = "Test",
    status: ComplianceStatus = ComplianceStatus.NON_COMPLIANT,
    severity: Severity = Severity.IMPORTANT,
    contract_quote: str = "Supplier may pass recruitment fees to workers.",
    policy_quote: str = "Forced labor is prohibited.",
    rationale: str = "Contract permits fees that policy prohibits.",
    confidence: float | None = 0.9,
) -> SectionCompareItem:
    return SectionCompareItem(
        section_id=section_id,
        policy_document_id=policy_document_id,
        dimension_label=dimension_label,
        status=status,
        severity=severity,
        contract_quote=contract_quote,
        policy_quote=policy_quote,
        rationale=rationale,
        confidence=confidence,
    )


def test_dedupe_same_quote_different_label() -> None:
    left = _item(dimension_label="Forced Labor", policy_document_id="doc-a")
    right = _item(
        dimension_label="Recruitment Fees",
        policy_document_id="doc-b",
        severity=Severity.CRITICAL,
    )
    deduped, removed = dedupe_compare_items([left, right], across_policies=True)
    assert removed == 1
    assert len(deduped) == 1
    assert deduped[0].severity == Severity.CRITICAL


def test_dedupe_keeps_different_quotes() -> None:
    left = _item(contract_quote="Supplier may pass recruitment fees to workers.")
    right = _item(
        contract_quote="Supplier is not required to maintain a formal human rights program.",
        dimension_label="Due Diligence",
    )
    deduped, removed = dedupe_compare_items([left, right])
    assert removed == 0
    assert len(deduped) == 2


def test_dedupe_keeps_different_status() -> None:
    quote = "Supplier may pass recruitment fees to workers."
    left = _item(status=ComplianceStatus.NON_COMPLIANT, contract_quote=quote)
    right = _item(
        status=ComplianceStatus.COMPLIANT,
        contract_quote=quote,
        dimension_label="Recruitment Fees",
        rationale="Contract aligns with local law requirements here.",
    )
    deduped, removed = dedupe_compare_items([left, right])
    assert removed == 0
    assert len(deduped) == 2


def test_cap_drops_compliant_first() -> None:
    items = [
        _item(dimension_label=f"NC {idx}", severity=Severity.IMPORTANT)
        for idx in range(4)
    ] + [
        _item(
            dimension_label=f"OK {idx}",
            status=ComplianceStatus.COMPLIANT,
            severity=Severity.INFO,
            rationale="Contract aligns with the cited policy requirement.",
        )
        for idx in range(2)
    ]
    capped, removed, warnings = cap_compare_items_by_section(items, 4)
    assert removed == 2
    assert len(capped) == 4
    assert all(item.status == ComplianceStatus.NON_COMPLIANT for item in capped)
    assert warnings


def test_cap_never_drops_critical_nc() -> None:
    items = [
        _item(
            dimension_label=f"Critical {idx}",
            severity=Severity.CRITICAL,
            contract_quote=f"critical gap number {idx} in contract text here",
        )
        for idx in range(5)
    ]
    capped, removed, warnings = cap_compare_items_by_section(items, 4)
    assert removed == 0
    assert len(capped) == 5
    assert not warnings


def test_gap_items_not_deduped() -> None:
    gap = SectionCompareItem(
        section_id="s-gap",
        dimension_label="Gap",
        status=ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
        severity=Severity.INFO,
        rationale="No relevant policy text was provided for this contract section.",
    )
    assert is_gap_compare_item(gap)
    deduped, removed = dedupe_compare_items([gap, gap])
    assert removed == 0
    assert len(deduped) == 2

    capped, removed_cap, _warnings = cap_compare_items_by_section([gap, gap], 1)
    assert removed_cap == 0
    assert len(capped) == 2


def test_suppress_contradicted_non_compliant_across_sections() -> None:
    compliant = _item(
        section_id="3.2",
        dimension_label="Secure Deletion",
        status=ComplianceStatus.COMPLIANT,
        contract_quote="Securely delete all Confidential Information",
    )
    conflict = _item(
        section_id="2.1",
        dimension_label="Secure Deletion",
        status=ComplianceStatus.NON_COMPLIANT,
        contract_quote="Hold all Confidential Information in strict confidence",
    )
    kept, removed = suppress_contradicted_non_compliant([compliant, conflict])
    assert removed == 1
    assert len(kept) == 1
    assert kept[0].status == ComplianceStatus.COMPLIANT


def test_suppress_contradicted_with_mismatched_dimension_labels() -> None:
    from review_agent.services.finding_dedupe import dimension_topic_key

    assert dimension_topic_key("Secure Deletion of Confidential Information") == (
        dimension_topic_key("Secure Deletion Requirements")
    )
    compliant = _item(
        section_id="3.2",
        dimension_label="Secure Deletion Requirements",
        status=ComplianceStatus.COMPLIANT,
        contract_quote="Securely delete all Confidential Information",
    )
    conflict = _item(
        section_id="2.1",
        dimension_label="Secure Deletion of Confidential Information",
        status=ComplianceStatus.NON_COMPLIANT,
        contract_quote="Hold all Confidential Information in strict confidence",
    )
    kept, removed = suppress_contradicted_non_compliant([compliant, conflict])
    assert removed == 1
    assert len(kept) == 1
    assert kept[0].section_id == "3.2"
