"""Thin universal validation for obligation compare items (Phase R7)."""

from __future__ import annotations

from document_core.schemas.compliance import ComplianceStatus, Severity
from review_agent.schemas.obligation import ContractObligation
from review_agent.schemas.obligation_compare import ObligationCompareItem


def _ipc_item(
    item: ObligationCompareItem,
    *,
    reason: str,
) -> ObligationCompareItem:
    return item.model_copy(
        update={
            "status": ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
            "severity": Severity.INFO,
            "policy_quote": "",
            "rationale": f"Routing validation blocked compare: {reason}"[:2000],
            "confidence": 0.85,
        }
    )


def validate_obligation_compare_items(
    items: list[ObligationCompareItem],
    *,
    obligations_by_id: dict[str, ContractObligation],
    allowed_doc_ids: set[str],
    candidate_doc_ids_by_obligation: dict[str, set[str]],
) -> tuple[list[ObligationCompareItem], list[str], int]:
    validated: list[ObligationCompareItem] = []
    warnings: list[str] = []
    rejected = 0

    for item in items:
        obligation = obligations_by_id.get(item.obligation_id)
        if obligation and obligation.is_boilerplate:
            if item.status == ComplianceStatus.NON_COMPLIANT:
                validated.append(_ipc_item(item, reason="boilerplate_obligation"))
                rejected += 1
                continue

        candidates = candidate_doc_ids_by_obligation.get(item.obligation_id, set())
        policy_id = str(item.policy_document_id or "").strip()
        if (
            policy_id
            and candidates
            and policy_id not in candidates
            and item.status in (ComplianceStatus.COMPLIANT, ComplianceStatus.NON_COMPLIANT)
        ):
            validated.append(_ipc_item(item, reason="no_invented_policies"))
            rejected += 1
            warnings.append(
                f"obligation {item.obligation_id}: policy {policy_id} outside candidate fence"
            )
            continue

        if (
            policy_id
            and allowed_doc_ids
            and policy_id not in allowed_doc_ids
            and item.status in (ComplianceStatus.COMPLIANT, ComplianceStatus.NON_COMPLIANT)
        ):
            validated.append(_ipc_item(item, reason="tenant_doc_missing"))
            rejected += 1
            continue

        validated.append(item)

    return validated, warnings, rejected
