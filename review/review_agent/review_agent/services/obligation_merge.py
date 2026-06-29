"""Merge obligation compare items into ComplianceFinding list (Phase R6)."""

from __future__ import annotations

import uuid
from typing import Any
from uuid import UUID

from document_core.schemas.compliance import ComplianceFinding, ComplianceStatus
from review_agent.config import ReviewSettings, get_settings
from review_agent.schemas.obligation_compare import ObligationCompareItem
from review_agent.schemas.section_compare import SectionCompareItem
from review_agent.services.finding_dedupe import dedupe_compare_items
from review_agent.services.playbook_context import PlaybookHints


def obligation_items_to_findings(
    items: list[ObligationCompareItem],
    *,
    routing_audit_by_obligation: dict[str, dict[str, Any]] | None = None,
    hints_by_document: dict[str, PlaybookHints] | None = None,
    settings: ReviewSettings | None = None,
) -> list[ComplianceFinding]:
    cfg = settings or get_settings()
    audits = routing_audit_by_obligation or {}

    shims = [
        SectionCompareItem(
            section_id=item.section_id,
            policy_document_id=item.policy_document_id,
            policy_section_id=item.policy_section_id,
            dimension_label=item.dimension_label or item.obligation_id,
            status=item.status,
            severity=item.severity,
            contract_quote=item.contract_quote,
            policy_quote=item.policy_quote,
            rationale=item.rationale,
            confidence=item.confidence,
        )
        for item in items
    ]
    deduped, _ = dedupe_compare_items(shims, across_policies=cfg.finding_dedupe_across_policies)

    shim_by_key = {
        (s.section_id, s.dimension_label, s.policy_document_id, s.status.value): s
        for s in deduped
    }
    item_by_key = {
        (i.section_id, i.dimension_label or i.obligation_id, i.policy_document_id, i.status.value): i
        for i in items
    }

    findings: list[ComplianceFinding] = []
    for key, shim in shim_by_key.items():
        item = item_by_key.get(key)
        if item is None:
            continue
        policy_doc: UUID | None = None
        if item.policy_document_id:
            try:
                policy_doc = UUID(str(item.policy_document_id))
            except ValueError:
                policy_doc = None

        hints = hints_by_document.get(str(policy_doc)) if policy_doc and hints_by_document else None
        source = (
            "obligation_ipc"
            if item.status == ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT
            else "obligation_compare"
        )
        metadata: dict[str, Any] = {
            "compliance_mode": "obligation_routing",
            "obligation_id": item.obligation_id,
            "source": source,
            "confidence": item.confidence,
            "routing_audit": audits.get(item.obligation_id, {}),
        }
        if hints and hints.policy_ref:
            metadata["policy_ref"] = hints.policy_ref
        if item.status in (
            ComplianceStatus.COMPLIANT,
            ComplianceStatus.NON_COMPLIANT,
        ) and (item.contract_quote or item.policy_quote):
            metadata["quote_validated_at_compare"] = True

        findings.append(
            ComplianceFinding(
                finding_id=str(uuid.uuid4()),
                dimension_id=f"{item.section_id}:{item.obligation_id}:{item.policy_section_id or 'general'}",
                dimension_label=item.dimension_label or item.obligation_id,
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
