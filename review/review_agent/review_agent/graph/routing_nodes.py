"""Semantic routing and catalog match graph nodes (Phase R2/R3)."""

from __future__ import annotations

from typing import Any

from review_agent.clients.document_client import DocumentMCPClient
from review_agent.config import get_settings
from review_agent.schemas.obligation import ContractObligation
from review_agent.schemas.routing_plan import ObligationRoutingPlan
from review_agent.services.catalog_alias_match import AliasMatchResult, match_explicit_mentions
from review_agent.services.catalog_matcher import match_obligation_to_catalog
from review_agent.services.catalog_registry import indexed_doc_id_set
from review_agent.services.routing_cache import get_catalog_snapshot
from review_agent.services.routing_limits import reset_routing_limits
from review_agent.services.routing_tenant import obligation_routing_active
from review_agent.services.semantic_routing_planner import _fallback_plan, plan_obligation_routing
from review_agent.observability import metrics
from review_agent.state.review_state import ReviewState


def _skipped_plan(ob: ContractObligation) -> ObligationRoutingPlan:
    return ObligationRoutingPlan(
        obligation_id=ob.obligation_id,
        explicit_policy_mentions=list(ob.explicit_policy_mentions),
        routing_source="skipped_boilerplate",
        confidence=0.0,
        reasoning="boilerplate obligation",
    )


def _plan_from_alias(ob: ContractObligation, alias: AliasMatchResult) -> ObligationRoutingPlan:
    return ObligationRoutingPlan(
        obligation_id=ob.obligation_id,
        intent=alias.title or (ob.text or "")[:80],
        explicit_policy_mentions=list(ob.explicit_policy_mentions),
        confidence=1.0,
        reasoning=f"registry alias match: {alias.matched_mention} -> {alias.title}",
        routing_source="registry_alias",
        resolved_document_ids=[alias.document_id],
    )


async def semantic_route_node(
    state: ReviewState,
    client: DocumentMCPClient,
) -> dict[str, Any]:
    settings = get_settings()
    tenant_id = state["tenant_id"]
    if not obligation_routing_active(tenant_id, settings):
        return {}

    reset_routing_limits()
    obligations = [
        ContractObligation.model_validate(item) for item in (state.get("obligations") or [])
    ]
    if not obligations:
        return {}

    catalog_snapshot = await get_catalog_snapshot(client, tenant_id, settings=settings)
    catalog = catalog_snapshot.entries
    catalog_version = catalog_snapshot.catalog_version
    plans: dict[str, ObligationRoutingPlan] = {}
    alias_hit_count = 0

    for ob in obligations:
        if ob.is_boilerplate or not (ob.text or "").strip():
            plans[ob.obligation_id] = _skipped_plan(ob)
            continue
        alias = match_explicit_mentions(
            ob.explicit_policy_mentions,
            catalog,
            min_score=settings.routing_alias_min_score,
        )
        if alias and alias.confidence >= settings.routing_alias_min_score:
            plans[ob.obligation_id] = _plan_from_alias(ob, alias)
            alias_hit_count += 1
            metrics.record_routing_alias_hit()
            continue

    remaining = [ob for ob in obligations if ob.obligation_id not in plans]
    if remaining and settings.semantic_planner_enabled:
        plans.update(
            await plan_obligation_routing(
                remaining,
                contract_type=state.get("contract_type"),
                catalog_entries=catalog,
                settings=settings,
                tenant_id=tenant_id,
                catalog_version=catalog_version,
            )
        )
    elif remaining:
        for ob in remaining:
            plans[ob.obligation_id] = _fallback_plan(ob)

    ipc_count = sum(
        1
        for plan in plans.values()
        if plan.routing_source == "skipped_boilerplate"
        or plan.confidence < settings.routing_ipc_max_confidence
    )
    stats = dict(state.get("obligation_extract_stats") or {})
    stats.update(
        {
            "obligation_routed_count": len(plans),
            "obligation_alias_hit_count": alias_hit_count,
            "obligation_ipc_route_count": ipc_count,
        }
    )
    compliance_stats = dict(state.get("compliance_stats") or {})
    compliance_stats.update(
        {
            "obligation_routed_count": len(plans),
            "obligation_alias_hit_count": alias_hit_count,
            "obligation_ipc_route_count": ipc_count,
        }
    )

    return {
        "obligation_routing_by_id": {
            key: value.model_dump(mode="json") for key, value in plans.items()
        },
        "obligation_extract_stats": stats,
        "compliance_stats": compliance_stats,
    }


async def catalog_match_node(
    state: ReviewState,
    client: DocumentMCPClient,
) -> dict[str, Any]:
    settings = get_settings()
    tenant_id = state["tenant_id"]
    if not obligation_routing_active(tenant_id, settings):
        return {}

    routing_by_id = state.get("obligation_routing_by_id") or {}
    if not routing_by_id:
        return {}

    catalog_snapshot = await get_catalog_snapshot(client, tenant_id, settings=settings)
    catalog = catalog_snapshot.entries
    allowed = indexed_doc_id_set(catalog)
    matches: dict[str, Any] = {}
    candidate_counts: list[int] = []

    for obligation_id, raw in routing_by_id.items():
        plan = ObligationRoutingPlan.model_validate(raw)
        match = await match_obligation_to_catalog(
            plan,
            client=client,
            tenant_id=state["tenant_id"],
            catalog_entries=catalog,
            allowed_doc_ids=allowed,
            settings=settings,
        )
        matches[obligation_id] = match
        candidate_counts.append(len(match.candidate_doc_ids))

    union_ids = sorted(
        {
            doc_id
            for match in matches.values()
            for doc_id in match.candidate_doc_ids
        }
    )
    avg_candidates = round(sum(candidate_counts) / len(candidate_counts), 2) if candidate_counts else 0.0

    stats = dict(state.get("obligation_extract_stats") or {})
    stats["obligation_catalog_match_avg_candidates"] = avg_candidates
    compliance_stats = dict(state.get("compliance_stats") or {})
    compliance_stats["obligation_catalog_match_avg_candidates"] = avg_candidates

    updates: dict[str, Any] = {
        "obligation_catalog_match_by_id": {
            key: value.model_dump(mode="json") for key, value in matches.items()
        },
        "obligation_extract_stats": stats,
        "compliance_stats": compliance_stats,
    }
    if union_ids:
        updates["obligation_routing_candidate_doc_ids"] = union_ids
    return updates
