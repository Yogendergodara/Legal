"""Merge section-first LLM items into ComplianceFinding list."""

from __future__ import annotations

import uuid
from uuid import UUID

from document_core.schemas.compliance import ComplianceFinding, ComplianceStatus, Severity
from review_agent.schemas.section_compare import SectionCompareItem
from review_agent.schemas.section_retrieval import SectionRetrievalBundle


def section_items_to_findings(
    items: list[SectionCompareItem],
    *,
    pipeline: str = "section_first",
) -> list[ComplianceFinding]:
    findings: list[ComplianceFinding] = []
    seen: set[tuple[str, str, str]] = set()

    for item in items:
        policy_doc: UUID | None = None
        if item.policy_document_id:
            try:
                policy_doc = UUID(str(item.policy_document_id))
            except ValueError:
                policy_doc = None
        key = (item.section_id, str(policy_doc or ""), item.dimension_label)
        if key in seen:
            continue
        seen.add(key)

        findings.append(
            ComplianceFinding(
                finding_id=str(uuid.uuid4()),
                dimension_id=f"{item.section_id}:{item.policy_section_id or 'general'}",
                dimension_label=item.dimension_label or item.section_id,
                status=item.status,
                severity=item.severity,
                contract_quote=item.contract_quote,
                policy_quote=item.policy_quote,
                contract_section_id=item.section_id,
                policy_section_id=item.policy_section_id or None,
                policy_document_id=policy_doc,
                rationale=item.rationale,
                metadata={
                    "compliance_mode": pipeline,
                    "confidence": item.confidence,
                },
            )
        )
    return findings


def findings_for_no_policy_sections(
    bundles: dict[str, SectionRetrievalBundle],
    compare_items: list[SectionCompareItem],
) -> list[ComplianceFinding]:
    """Sections with zero policy hits and no LLM compare output → explicit insufficient context."""
    compared_section_ids = {item.section_id for item in compare_items}
    findings: list[ComplianceFinding] = []
    for section_id, bundle in bundles.items():
        if bundle.policy_hits or section_id in compared_section_ids:
            continue
        findings.append(
            ComplianceFinding(
                finding_id=str(uuid.uuid4()),
                dimension_id=f"{section_id}:no_policy",
                dimension_label=f"Section {section_id} — no policy retrieved",
                status=ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
                severity=Severity.INFO,
                contract_section_id=section_id,
                rationale=(
                    "No relevant policy sections were retrieved for this contract section "
                    f"(categories tried: {', '.join(bundle.categories) or 'general'})."
                ),
                metadata={"compliance_mode": "section_first", "gap_type": "no_policy"},
            )
        )
    return findings


def merge_section_findings(
    compare_items: list[SectionCompareItem],
    bundles: dict[str, SectionRetrievalBundle],
) -> tuple[list[ComplianceFinding], list[str]]:
    """Dedupe compare items + add no-policy gaps."""
    findings = section_items_to_findings(compare_items)
    gap_findings = findings_for_no_policy_sections(bundles, compare_items)
    warnings: list[str] = []
    if gap_findings:
        warnings.append(
            f"{len(gap_findings)} contract section(s) had no retrieved policy context."
        )
    merged = findings + gap_findings
    return merged, warnings
