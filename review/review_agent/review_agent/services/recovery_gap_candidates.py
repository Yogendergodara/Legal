"""Promote hit-backed IPC sections into F5 compare_omitted recovery (RC-07)."""

from __future__ import annotations

from document_core.schemas.compliance import ComplianceFinding, ComplianceStatus
from review_agent.schemas.section_compare import SectionCompareItem
from review_agent.schemas.section_retrieval import SectionRetrievalBundle

_RESOLVED_COMPARE_SOURCES = frozenset(
    {"playbook_compare", "section_first_final", "obligation_compare"}
)
_IPC_STATUSES = frozenset(
    {
        ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
        ComplianceStatus.INCONCLUSIVE,
    }
)


def _section_has_resolved_compare(
    section_id: str,
    *,
    compare_items: list[SectionCompareItem],
    findings: list[ComplianceFinding],
) -> bool:
    for item in compare_items:
        if item.section_id != section_id:
            continue
        if item.status in (ComplianceStatus.NON_COMPLIANT, ComplianceStatus.COMPLIANT):
            return True
    for finding in findings:
        if finding.contract_section_id != section_id:
            continue
        if finding.status not in (ComplianceStatus.NON_COMPLIANT, ComplianceStatus.COMPLIANT):
            continue
        source = str((finding.metadata or {}).get("source") or "")
        if source in _RESOLVED_COMPARE_SOURCES:
            return True
    return False


def promote_recovery_compare_omitted_gaps(
    *,
    compare_items: list[SectionCompareItem],
    bundles: dict[str, SectionRetrievalBundle],
    obligation_findings: list[ComplianceFinding],
    section_findings: list[ComplianceFinding],
    compare_omitted_gap_ids: list[str],
    gap_section_ids: list[str],
    enabled: bool = True,
) -> tuple[list[str], list[str], list[str]]:
    """Return updated gap lists and promoted section ids (idempotent)."""
    if not enabled:
        return list(compare_omitted_gap_ids), list(gap_section_ids), []

    all_findings = list(section_findings) + list(obligation_findings)
    omitted_set = set(compare_omitted_gap_ids)
    promoted: list[str] = []

    for section_id, bundle in bundles.items():
        if not bundle.policy_hits or section_id in omitted_set:
            continue
        if _section_has_resolved_compare(
            section_id,
            compare_items=compare_items,
            findings=all_findings,
        ):
            continue
        section_scoped = [f for f in all_findings if f.contract_section_id == section_id]
        compared = any(item.section_id == section_id for item in compare_items)
        if not section_scoped and not compared:
            continue
        if section_scoped and not all(f.status in _IPC_STATUSES for f in section_scoped):
            continue
        promoted.append(section_id)
        omitted_set.add(section_id)

    compare_omitted = list(dict.fromkeys([*compare_omitted_gap_ids, *promoted]))
    gap_ids = list(dict.fromkeys([*gap_section_ids, *promoted]))
    return compare_omitted, gap_ids, promoted
