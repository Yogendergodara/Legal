"""Obligation compare graph node (Phase R6 + R7 validation)."""

from __future__ import annotations

from typing import Any

from document_core.schemas.chunk import IndexedChunk
from document_core.schemas.compliance import ComplianceStatus
from review_agent.clients.document_client import DocumentMCPClient
from review_agent.config import get_settings
from review_agent.errors import FatalPipelineError
from review_agent.schemas.evidence_sufficiency import EvidenceSufficiencyResult
from review_agent.schemas.obligation import ContractObligation
from review_agent.schemas.obligation_retrieval import ObligationRetrievalBundle
from review_agent.schemas.routing_plan import CatalogMatchResult, ObligationRoutingPlan
from review_agent.services.obligation_compare_llm import compare_obligations_batch, ipc_item_from_evidence
from review_agent.services.obligation_merge import obligation_items_to_findings
from review_agent.services.playbook_context import build_playbook_hints_by_document
from review_agent.services.routing_audit import build_routing_audit
from review_agent.services.routing_summary import build_routing_summary
from review_agent.services.routing_tenant import obligation_routing_active
from review_agent.services.routing_validation import validate_obligation_compare_items
from review_agent.observability import metrics
from review_agent.state.review_state import ReviewState


async def obligation_compare_node(
    state: ReviewState,
    client: DocumentMCPClient,
) -> dict[str, Any]:
    settings = get_settings()
    try:
        return await _obligation_compare_impl(state, client, settings)
    except FatalPipelineError:
        raise
    except Exception as exc:  # noqa: BLE001
        if not settings.compare_branch_fail_open:
            raise
        prior = dict(state.get("compliance_stats") or {})
        return {
            "obligation_compare_items": [],
            "obligation_findings": [],
            "compliance_stats": {
                **prior,
                "obligation_compare_failed": True,
                "obligation_compare_fail_reason": str(exc)[:500],
            },
            "warnings": [f"obligation compare branch failed (fail-open): {exc}"],
        }


async def _obligation_compare_impl(
    state: ReviewState,
    client: DocumentMCPClient,
    settings,
) -> dict[str, Any]:
    _ = client
    if not obligation_routing_active(state["tenant_id"], settings) or not settings.obligation_compare_enabled:
        return {}

    obligations = [
        ContractObligation.model_validate(item) for item in (state.get("obligations") or [])
    ]
    if not obligations:
        return {}

    evidence_raw = state.get("obligation_evidence_by_id") or {}
    plans_raw = state.get("obligation_routing_by_id") or {}
    matches_raw = state.get("obligation_catalog_match_by_id") or {}
    retrieval_raw = state.get("obligation_retrieval_by_id") or {}
    indexed_policies = list(state.get("indexed_policies") or [])

    allowed_doc_ids = {
        str(entry.get("document_id") or "").strip()
        for entry in indexed_policies
        if str(entry.get("document_id") or "").strip()
    }
    obligations_by_id = {ob.obligation_id: ob for ob in obligations}
    audits: dict[str, dict[str, Any]] = {}
    candidate_doc_ids_by_obligation: dict[str, set[str]] = {}
    evidence_by_id: dict[str, EvidenceSufficiencyResult] = {}
    hits_by_obligation: dict[str, list] = {}
    compare_queue: list[ContractObligation] = []
    items = []

    for ob in obligations:
        obligation_id = ob.obligation_id
        if obligation_id not in evidence_raw:
            continue
        plan = ObligationRoutingPlan.model_validate(plans_raw[obligation_id])
        match = CatalogMatchResult.model_validate(matches_raw[obligation_id])
        bundle = (
            ObligationRetrievalBundle.model_validate(retrieval_raw[obligation_id])
            if obligation_id in retrieval_raw
            else None
        )
        evidence = EvidenceSufficiencyResult.model_validate(evidence_raw[obligation_id])
        evidence_by_id[obligation_id] = evidence
        candidate_doc_ids_by_obligation[obligation_id] = set(match.candidate_doc_ids)
        audits[obligation_id] = build_routing_audit(
            obligation_id=obligation_id,
            section_id=ob.section_id,
            plan=plan,
            match=match,
            bundle=bundle,
            evidence=evidence,
            indexed_policies=indexed_policies,
        )
        hits_by_obligation[obligation_id] = list(evidence.final_hits)
        if evidence.decision == "compare":
            compare_queue.append(ob)
            metrics.record_routing_compare()
        else:
            metrics.record_routing_ipc()
            items.append(ipc_item_from_evidence(ob, evidence, plan=plan, match=match))

    sections_by_id: dict[str, IndexedChunk] = {}
    for raw in state.get("contract_sections") or []:
        section = (
            raw if isinstance(raw, IndexedChunk) else IndexedChunk.model_validate(raw)
        )
        sections_by_id[section.section_id] = section

    compare_warnings: list[str] = []
    compare_stats: dict[str, int] = {"obligation_compare_llm_batches": 0}
    llm_items: list = []
    if compare_queue:
        llm_items, compare_warnings, compare_stats = await compare_obligations_batch(
            compare_queue,
            evidence_by_id,
            hits_by_obligation,
            contract_type=state.get("contract_type"),
            memory_context=state.get("memory_context") or "",
            settings=settings,
            playbook_hints_by_document=build_playbook_hints_by_document(indexed_policies),
            sections_by_id=sections_by_id,
        )
        items.extend(llm_items)

    llm_ipc_count = sum(
        1 for item in llm_items if item.status == ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT
    )

    validated, validation_warnings, rejected = validate_obligation_compare_items(
        items,
        obligations_by_id=obligations_by_id,
        allowed_doc_ids=allowed_doc_ids,
        candidate_doc_ids_by_obligation=candidate_doc_ids_by_obligation,
    )
    compare_warnings.extend(validation_warnings)
    if rejected:
        metrics.record_wrong_policy_blocked()

    findings = obligation_items_to_findings(
        validated,
        routing_audit_by_obligation=audits,
        hints_by_document=build_playbook_hints_by_document(indexed_policies),
        settings=settings,
    )

    ipc_count = sum(1 for item in validated if item.status.value == "INSUFFICIENT_POLICY_CONTEXT")
    compare_count = len(validated) - ipc_count
    extract_stats = dict(state.get("obligation_extract_stats") or {})
    unique_obligation_ids = {item.obligation_id for item in llm_items}
    over_cap_ids = [
        oid
        for oid in unique_obligation_ids
        if sum(1 for item in llm_items if item.obligation_id == oid) > 2
    ]
    if over_cap_ids:
        compare_warnings.append(
            f"obligation compare returned >2 items for: {', '.join(sorted(over_cap_ids)[:5])}"
        )

    prior_stats = dict(state.get("compliance_stats") or {})
    evidence_skip = dict(prior_stats.get("obligation_evidence_skip_by_reason") or {})
    compliance_stats = dict(prior_stats)
    compliance_stats.update(
        {
            "obligation_compare_count": compare_count,
            "obligation_ipc_findings": ipc_count,
            "obligation_compare_llm_ipc_count": llm_ipc_count,
            "obligation_compare_llm_calls": compare_stats.get("obligation_compare_llm_batches", 0),
            "routing_validation_rejected": rejected,
            "compliance_mode": "obligation_routing",
            "obligation_pipeline_funnel": {
                "extracted": len(obligations),
                "compare_queued": len(compare_queue),
                "compare_pre_ipc": len(obligations) - len(compare_queue),
                "llm_batches": compare_stats.get("obligation_compare_llm_batches", 0),
                "llm_batches_failed": compare_stats.get("obligation_compare_llm_batches_failed", 0),
                "llm_items_returned": len(llm_items),
                "llm_unique_obligations_returned": len(unique_obligation_ids),
                "llm_ipc_count": llm_ipc_count,
                "omitted_from_batch": compare_stats.get("obligation_compare_omitted", 0),
                "single_retry_batches": compare_stats.get("obligation_compare_single_retries", 0),
                "single_recovered": compare_stats.get("obligation_compare_single_recovered", 0),
                "policy_id_backfilled": compare_stats.get("obligation_compare_policy_id_backfilled", 0),
                "post_validation_compared": compare_count,
                "post_validation_ipc": ipc_count,
                "skip_by_reason": evidence_skip,
            },
            "routing_summary": build_routing_summary(
                obligation_count=len(obligations),
                alias_hit_count=int(extract_stats.get("obligation_alias_hit_count") or 0),
                ipc_count=ipc_count,
                compare_count=compare_count,
                wrong_policy_blocked=rejected,
                planner_calls_snapshot=prior_stats.get("routing_planner_calls"),
                catalog_search_calls_snapshot=prior_stats.get("routing_catalog_search_calls"),
            ),
        }
    )

    return {
        "obligation_compare_items": [item.model_dump(mode="json") for item in validated],
        "obligation_findings": [finding.model_dump(mode="json") for finding in findings],
        "compliance_stats": compliance_stats,
        "warnings": compare_warnings,
    }
