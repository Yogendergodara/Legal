"""Merge section-first LLM items into ComplianceFinding list."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from uuid import UUID

from document_core.schemas.chunk import IndexedChunk
from document_core.schemas.compliance import ComplianceFinding, ComplianceStatus, Severity
from review_agent.config import ReviewSettings, get_settings
from review_agent.schemas.section_compare import SectionCompareItem
from review_agent.schemas.section_retrieval import SectionRetrievalBundle
from review_agent.services.finding_dedupe import dedupe_compare_items, prepare_compare_items_for_merge
from review_agent.services.playbook_context import PlaybookHints
from review_agent.services.section_gap_status import resolve_gap_finding_status
from review_agent.services.quote_validate import QUOTE_VALIDATE_DOWNGRADE_MARKER
from review_agent.services.unclear_recompare import classify_unclear_finding, eligible_for_unclear_recompare

_UNCLEAR_STATUSES = frozenset(
    {ComplianceStatus.INCONCLUSIVE, ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT}
)
_UNCLEAR_CONFIDENCE_MAX = 0.5


@dataclass
class MergeSectionResult:
    findings: list[ComplianceFinding] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    gap_section_ids: list[str] = field(default_factory=list)
    no_policy_gap_ids: list[str] = field(default_factory=list)
    compare_omitted_gap_ids: list[str] = field(default_factory=list)
    unclear_finding_ids: list[str] = field(default_factory=list)
    unclear_recompare_finding_ids: list[str] = field(default_factory=list)
    conflict_pairs: list[tuple[str, str]] = field(default_factory=list)


def section_items_to_findings(
    items: list[SectionCompareItem],
    *,
    pipeline: str = "section_first",
    hints_by_document: dict[str, PlaybookHints] | None = None,
    dedupe: bool = True,
    settings: ReviewSettings | None = None,
) -> list[ComplianceFinding]:
    if dedupe:
        cfg = settings or get_settings()
        items, _ = dedupe_compare_items(
            items,
            across_policies=cfg.finding_dedupe_across_policies,
        )

    findings: list[ComplianceFinding] = []

    for item in items:
        policy_doc: UUID | None = None
        if item.policy_document_id:
            try:
                policy_doc = UUID(str(item.policy_document_id))
            except ValueError:
                policy_doc = None

        hints = None
        if policy_doc and hints_by_document:
            hints = hints_by_document.get(str(policy_doc))
        metadata: dict = {
            "compliance_mode": pipeline,
            "confidence": item.confidence,
        }
        if pipeline == "section_first":
            metadata["source"] = "playbook_compare"
            if hints:
                if hints.policy_ref:
                    metadata["policy_ref"] = hints.policy_ref
                metadata["playbook_guidance_used"] = bool(
                    hints.review_guidance or hints.preferred_position
                )

        rationale = item.rationale or ""
        if rationale.startswith("Section compare failed:"):
            metadata["gap_type"] = "compare_failed"
            metadata["source"] = "section_compare_failed"
        if QUOTE_VALIDATE_DOWNGRADE_MARKER in rationale:
            metadata["downgrade_source"] = "quote_validate"

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
                metadata=metadata,
            )
        )
    return findings


def findings_for_no_policy_sections(
    bundles: dict[str, SectionRetrievalBundle],
    compare_items: list[SectionCompareItem],
    *,
    sections_by_id: dict[str, IndexedChunk] | None = None,
    settings: ReviewSettings | None = None,
) -> list[ComplianceFinding]:
    cfg = settings or get_settings()
    compared_section_ids = {item.section_id for item in compare_items}
    findings: list[ComplianceFinding] = []
    for section_id, bundle in bundles.items():
        if section_id in compared_section_ids:
            continue
        has_policy = bool(bundle.policy_hits)
        gap_type = "compare_omitted" if has_policy else "no_policy"
        section = (sections_by_id or {}).get(section_id)
        if has_policy:
            rationale = (
                "Policy sections were retrieved but the compare step did not produce "
                f"a finding for this contract section (categories tried: "
                f"{', '.join(bundle.categories) or 'general'})."
            )
            label = f"Section {section_id} — compare omitted"
        else:
            rationale = (
                "No relevant policy sections were retrieved for this contract section "
                f"(categories tried: {', '.join(bundle.categories) or 'general'})."
            )
            label = f"Section {section_id} — no policy retrieved"
        if sections_by_id is not None and cfg.gap_status_substantive_inconclusive:
            status, review_outcome, suffix = resolve_gap_finding_status(
                section,
                gap_type=gap_type,
                categories=list(bundle.categories or []),
                settings=cfg,
            )
        else:
            status = ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT
            review_outcome = "playbook_gap"
            suffix = ""
        if suffix:
            rationale = f"{rationale.rstrip('.')}.{suffix}"
        findings.append(
            ComplianceFinding(
                finding_id=str(uuid.uuid4()),
                dimension_id=f"{section_id}:{gap_type}",
                dimension_label=label,
                status=status,
                severity=Severity.INFO,
                contract_section_id=section_id,
                rationale=rationale,
                metadata={
                    "compliance_mode": "section_first",
                    "gap_type": gap_type,
                    "review_outcome": review_outcome,
                },
            )
        )
    return findings


def _collect_unclear(findings: list[ComplianceFinding]) -> list[str]:
    unclear: list[str] = []
    for finding in findings:
        conf = finding.metadata.get("confidence")
        low_conf = conf is not None and float(conf) < _UNCLEAR_CONFIDENCE_MAX
        if finding.status in _UNCLEAR_STATUSES or low_conf:
            unclear.append(finding.finding_id)
    return unclear


def _collect_conflicts(findings: list[ComplianceFinding]) -> list[tuple[str, str]]:
    by_label: dict[str, list[ComplianceFinding]] = {}
    for finding in findings:
        label = (finding.dimension_label or "").strip().lower()
        if not label:
            continue
        by_label.setdefault(label, []).append(finding)

    pairs: list[tuple[str, str]] = []
    for group in by_label.values():
        statuses = {f.status for f in group}
        if len(statuses) <= 1:
            continue
        for i, left in enumerate(group):
            for right in group[i + 1 :]:
                if left.status != right.status:
                    pairs.append((left.finding_id, right.finding_id))
    return pairs


def merge_section_findings(
    compare_items: list[SectionCompareItem],
    bundles: dict[str, SectionRetrievalBundle],
    *,
    hints_by_document: dict[str, PlaybookHints] | None = None,
    sections_by_id: dict[str, IndexedChunk] | None = None,
    settings: ReviewSettings | None = None,
) -> MergeSectionResult:
    """Dedupe compare items, add gaps, tag unclear + conflicts."""
    cfg = settings or get_settings()
    processed_items, _deduped_count, _capped_count, prep_warnings = prepare_compare_items_for_merge(
        compare_items,
        settings=cfg,
    )
    findings = section_items_to_findings(
        processed_items,
        hints_by_document=hints_by_document,
        dedupe=False,
        settings=cfg,
    )
    gap_findings = findings_for_no_policy_sections(
        bundles,
        processed_items,
        sections_by_id=sections_by_id,
        settings=cfg,
    )
    warnings: list[str] = list(prep_warnings)
    if gap_findings:
        warnings.append(
            f"{len(gap_findings)} contract section(s) had no retrieved policy context."
        )

    merged = findings + gap_findings
    no_policy_gap_ids = [
        f.contract_section_id
        for f in gap_findings
        if f.contract_section_id and f.metadata.get("gap_type") == "no_policy"
    ]
    compare_omitted_gap_ids = [
        f.contract_section_id
        for f in gap_findings
        if f.contract_section_id and f.metadata.get("gap_type") == "compare_omitted"
    ]
    gap_section_ids = [
        f.contract_section_id
        for f in gap_findings
        if f.contract_section_id
    ]
    unclear_ids = _collect_unclear(merged)
    recompare_ids = [f.finding_id for f in merged if eligible_for_unclear_recompare(f)]
    if unclear_ids:
        warnings.append(f"{len(unclear_ids)} finding(s) marked unclear for final verify.")
    if unclear_ids and len(recompare_ids) < len(unclear_ids):
        skipped = len(unclear_ids) - len(recompare_ids)
        warnings.append(
            f"{skipped} unclear finding(s) skipped for re-compare (not low-confidence playbook compare)."
        )
    conflict_pairs = _collect_conflicts(merged)
    if conflict_pairs:
        warnings.append(f"{len(conflict_pairs)} cross-section status conflict(s) detected.")

    enriched: list[ComplianceFinding] = []
    for finding in merged:
        meta = dict(finding.metadata)
        if finding.finding_id in unclear_ids:
            meta["needs_final_verify"] = True
            meta["unclear_reason"] = classify_unclear_finding(finding)
            meta["unclear_recompare_eligible"] = finding.finding_id in recompare_ids
        for left_id, right_id in conflict_pairs:
            if finding.finding_id in (left_id, right_id):
                meta["conflict_group"] = left_id
        enriched.append(finding.model_copy(update={"metadata": meta}))

    return MergeSectionResult(
        findings=enriched,
        warnings=warnings,
        gap_section_ids=gap_section_ids,
        no_policy_gap_ids=no_policy_gap_ids,
        compare_omitted_gap_ids=compare_omitted_gap_ids,
        unclear_finding_ids=unclear_ids,
        unclear_recompare_finding_ids=recompare_ids,
        conflict_pairs=conflict_pairs,
    )
