"""Obligation retrieval and evidence sufficiency graph nodes (Phase R4/R5)."""

from __future__ import annotations

from typing import Any

from document_core.schemas.chunk import IndexedChunk
from document_core.schemas.policy_catalog import CatalogSearchRequest
from review_agent.clients.document_client import DocumentMCPClient
from review_agent.config import get_settings
from review_agent.schemas.evidence_sufficiency import EvidenceSufficiencyResult
from review_agent.schemas.obligation import ContractObligation
from review_agent.schemas.obligation_retrieval import ObligationRetrievalBundle
from review_agent.schemas.routing_plan import CatalogMatchResult, ObligationRoutingPlan
from review_agent.schemas.section_retrieval import SectionRetrievalBundle
from review_agent.services.async_limits import gather_limited
from review_agent.services.evidence_sufficiency import (
    evaluate_evidence_sufficiency,
    tally_skip_reasons,
)
from review_agent.services.obligation_retrieval import (
    retrieve_for_obligation,
    should_skip_obligation_for_resolved_section,
)
from review_agent.services.routing_tenant import obligation_routing_active
from review_agent.state.review_state import ReviewState


def _aggregate_obligation_retrieval_stats(
    bundles: dict[str, ObligationRetrievalBundle],
) -> dict[str, int]:
    mcp_calls = 0
    early_exit = 0
    queries_executed = 0
    queries_planned = 0
    for bundle in bundles.values():
        meta = bundle.retrieval_meta or {}
        steps = meta.get("query_steps") or []
        mcp_calls += len(steps) * 2
        queries_planned += int(meta.get("queries_planned") or len(bundle.queries_used or []))
        queries_executed += int(meta.get("queries_executed") or len(steps))
        if meta.get("ladder_early_exit"):
            early_exit += 1
    return {
        "obligation_retrieval_mcp_calls": mcp_calls,
        "obligation_retrieval_ladder_early_exit_count": early_exit,
        "obligation_retrieval_queries_executed_total": queries_executed,
        "obligation_retrieval_queries_planned_total": queries_planned,
    }


async def obligation_retrieval_node(
    state: ReviewState,
    client: DocumentMCPClient,
) -> dict[str, Any]:
    settings = get_settings()
    if not obligation_routing_active(state["tenant_id"], settings) or not settings.obligation_retrieval_enabled:
        return {}

    obligations = [
        ContractObligation.model_validate(item) for item in (state.get("obligations") or [])
    ]
    plans_raw = state.get("obligation_routing_by_id") or {}
    matches_raw = state.get("obligation_catalog_match_by_id") or {}
    if not obligations or not matches_raw:
        return {}

    policy_catalog = list(state.get("indexed_policies") or [])
    section_bundles_raw = state.get("section_retrieval_by_id") or {}
    sections_by_id: dict[str, IndexedChunk] = {}
    for raw in state.get("contract_sections") or []:
        section = (
            raw if isinstance(raw, IndexedChunk) else IndexedChunk.model_validate(raw)
        )
        sections_by_id[section.section_id] = section

    section_skip_count = 0
    section_hit_reuse_count = 0

    async def _retrieve(ob: ContractObligation) -> tuple[str, ObligationRetrievalBundle]:
        nonlocal section_skip_count, section_hit_reuse_count
        plan = ObligationRoutingPlan.model_validate(plans_raw[ob.obligation_id])
        match = CatalogMatchResult.model_validate(matches_raw[ob.obligation_id])
        section_bundle = None
        raw_section_bundle = section_bundles_raw.get(ob.section_id)
        if raw_section_bundle:
            section_bundle = SectionRetrievalBundle.model_validate(raw_section_bundle)
        if should_skip_obligation_for_resolved_section(
            tenant_id=state["tenant_id"],
            plan=plan,
            match=match,
            section_bundle=section_bundle,
            settings=settings,
        ):
            section_skip_count += 1
            return ob.obligation_id, ObligationRetrievalBundle(
                obligation_id=ob.obligation_id,
                section_id=ob.section_id,
                concepts=list(plan.concepts),
                skipped_reason="section_path_resolved",
                retrieval_meta={"section_path_resolved": True},
            )
        bundle = await retrieve_for_obligation(
            client,
            obligation=ob,
            plan=plan,
            match=match,
            tenant_id=state["tenant_id"],
            contract_type=state.get("contract_type"),
            policy_type=state.get("policy_type"),
            settings=settings,
            policy_catalog=policy_catalog,
            section=sections_by_id.get(ob.section_id),
            section_bundle=section_bundle,
        )
        if (bundle.retrieval_meta or {}).get("section_hit_reuse"):
            section_hit_reuse_count += 1
        return ob.obligation_id, bundle

    targets = [ob for ob in obligations if ob.obligation_id in matches_raw]
    results = await gather_limited(
        [_retrieve(ob) for ob in targets],
        limit=settings.obligation_retrieval_concurrency,
    )

    bundles: dict[str, ObligationRetrievalBundle] = {}
    for item in results:
        if isinstance(item, BaseException):
            continue
        obligation_id, bundle = item
        bundles[obligation_id] = bundle

    zero_hit = sum(
        1
        for bundle in bundles.values()
        if not bundle.skipped_reason and not bundle.policy_hits
    )
    compliance_stats = dict(state.get("compliance_stats") or {})
    compliance_stats.update(
        {
            "obligation_retrieved_count": len(bundles),
            "obligation_retrieval_zero_hit": zero_hit,
            "obligation_retrieval_section_skip_count": section_skip_count,
            "obligation_section_hit_reuse_count": section_hit_reuse_count,
            **_aggregate_obligation_retrieval_stats(bundles),
        }
    )

    return {
        "obligation_retrieval_by_id": {
            key: value.model_dump(mode="json") for key, value in bundles.items()
        },
        "compliance_stats": compliance_stats,
    }


async def evidence_sufficiency_node(
    state: ReviewState,
    client: DocumentMCPClient,
) -> dict[str, Any]:
    settings = get_settings()
    if not obligation_routing_active(state["tenant_id"], settings) or not settings.evidence_sufficiency_enabled:
        return {}

    bundles_raw = state.get("obligation_retrieval_by_id") or {}
    if not bundles_raw:
        return {}

    plans_raw = state.get("obligation_routing_by_id") or {}
    matches_raw = state.get("obligation_catalog_match_by_id") or {}
    obligations_by_id = {
        ob.obligation_id: ob
        for ob in (
            ContractObligation.model_validate(item) for item in (state.get("obligations") or [])
        )
    }
    policy_catalog = list(state.get("indexed_policies") or [])
    allowed_doc_ids = {
        str(entry.get("document_id") or entry.get("documentId") or "").strip()
        for entry in policy_catalog
        if str(entry.get("document_id") or entry.get("documentId") or "").strip()
    }
    sections_by_id: dict[str, IndexedChunk] = {}
    for raw in state.get("contract_sections") or []:
        section = (
            raw if isinstance(raw, IndexedChunk) else IndexedChunk.model_validate(raw)
        )
        sections_by_id[section.section_id] = section

    evidence: dict[str, EvidenceSufficiencyResult] = {}
    expand_count = 0
    expand_success = 0
    compare_ready = 0
    ipc_count = 0
    expand_jobs: list[tuple[str, ContractObligation, ObligationRoutingPlan, CatalogMatchResult, ObligationRetrievalBundle]] = []

    for obligation_id, raw_bundle in bundles_raw.items():
        obligation = obligations_by_id.get(obligation_id)
        if obligation is None:
            continue
        plan = ObligationRoutingPlan.model_validate(plans_raw[obligation_id])
        match = CatalogMatchResult.model_validate(matches_raw[obligation_id])
        bundle = ObligationRetrievalBundle.model_validate(raw_bundle)

        result = evaluate_evidence_sufficiency(
            obligation=obligation,
            plan=plan,
            match=match,
            bundle=bundle,
            settings=settings,
            expand_round=0,
        )

        if result.decision == "expand":
            expand_count += 1
            expand_jobs.append((obligation_id, obligation, plan, match, bundle))
        else:
            if result.decision == "compare":
                compare_ready += 1
            elif result.decision == "ipc":
                ipc_count += 1
            evidence[obligation_id] = result

    async def _expand_job(
        job: tuple[str, ContractObligation, ObligationRoutingPlan, CatalogMatchResult, ObligationRetrievalBundle],
    ) -> tuple[str, EvidenceSufficiencyResult, bool]:
        obligation_id, obligation, plan, match, bundle = job
        extra_queries = list(plan.concepts)
        extra_doc_ids: list[str] = []
        mode = settings.evidence_expand_broaden_mode
        if mode in ("catalog_neighbor", "both") and (plan.intent or obligation.text):
            query = (plan.intent or obligation.text or "")[:200]
            neighbors = await client.search_policy_catalog(
                CatalogSearchRequest(
                    tenant_id=state["tenant_id"],
                    query=query,
                    top_k=settings.evidence_expand_max_extra_docs,
                )
            )
            for hit in neighbors:
                doc_id = str(hit.document_id)
                if allowed_doc_ids and doc_id not in allowed_doc_ids:
                    continue
                extra_doc_ids.append(doc_id)

        expanded_bundle = await retrieve_for_obligation(
            client,
            obligation=obligation,
            plan=plan,
            match=match,
            tenant_id=state["tenant_id"],
            contract_type=state.get("contract_type"),
            policy_type=state.get("policy_type"),
            settings=settings,
            policy_catalog=policy_catalog,
            expand_mode=True,
            extra_doc_ids=extra_doc_ids,
            extra_queries=extra_queries,
            section=sections_by_id.get(obligation.section_id),
        )
        result = evaluate_evidence_sufficiency(
            obligation=obligation,
            plan=plan,
            match=match,
            bundle=expanded_bundle,
            settings=settings,
            expand_round=1,
        )
        return obligation_id, result, result.decision == "compare"

    if expand_jobs:
        expand_results = await gather_limited(
            [_expand_job(job) for job in expand_jobs],
            limit=settings.evidence_expand_concurrency,
        )
        for item in expand_results:
            if isinstance(item, BaseException):
                continue
            obligation_id, result, became_compare = item
            if became_compare:
                expand_success += 1
            if result.decision == "compare":
                compare_ready += 1
            elif result.decision == "ipc":
                ipc_count += 1
            evidence[obligation_id] = result

    compliance_stats = dict(state.get("compliance_stats") or {})
    compliance_stats.update(
        {
            "obligation_compare_ready_count": compare_ready,
            "obligation_evidence_ipc_count": ipc_count,
            "obligation_evidence_expand_count": expand_count,
            "obligation_evidence_expand_success": expand_success,
            "obligation_evidence_skip_by_reason": tally_skip_reasons(evidence),
        }
    )

    return {
        "obligation_evidence_by_id": {
            key: value.model_dump(mode="json") for key, value in evidence.items()
        },
        "compliance_stats": compliance_stats,
    }
