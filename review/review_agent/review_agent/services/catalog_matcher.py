"""Match obligation routing plans to tenant policy catalog (Phase R3)."""

from __future__ import annotations

from document_core.schemas.policy_catalog import CatalogSearchRequest
from review_agent.clients.document_client import DocumentMCPClient
from review_agent.config import ReviewSettings, get_settings
from review_agent.schemas.routing_plan import CatalogMatchResult, ObligationRoutingPlan
from review_agent.services.catalog_registry import CatalogEntry
from review_agent.services.routing_limits import catalog_search_calls, increment_catalog_search_calls


async def match_obligation_to_catalog(
    plan: ObligationRoutingPlan,
    *,
    client: DocumentMCPClient,
    tenant_id: str,
    catalog_entries: list[CatalogEntry],
    allowed_doc_ids: set[str] | None,
    settings: ReviewSettings | None = None,
) -> CatalogMatchResult:
    _ = catalog_entries
    cfg = settings or get_settings()
    allowed = allowed_doc_ids or set()

    if plan.routing_source == "skipped_boilerplate":
        return CatalogMatchResult(
            obligation_id=plan.obligation_id,
            routing_source="ipc",
            confidence=plan.confidence,
            route_decision="ipc",
        )

    if plan.confidence < cfg.routing_ipc_max_confidence:
        return CatalogMatchResult(
            obligation_id=plan.obligation_id,
            routing_source="ipc",
            confidence=plan.confidence,
            route_decision="ipc",
        )

    if plan.routing_source == "registry_alias":
        doc_ids = [doc_id for doc_id in plan.resolved_document_ids if doc_id in allowed]
        rejected = [
            {"document_id": doc_id, "reason": "not_in_tenant_registry"}
            for doc_id in plan.resolved_document_ids
            if doc_id not in allowed
        ]
        return CatalogMatchResult(
            obligation_id=plan.obligation_id,
            candidate_doc_ids=doc_ids,
            candidate_scores={doc_id: 1.0 for doc_id in doc_ids},
            routing_source="registry_alias",
            confidence=1.0,
            rejected=rejected,
            route_decision="compare" if doc_ids else "ipc",
        )

    queries = [q.strip() for q in (plan.search_queries or [plan.intent]) if str(q).strip()][:3]
    scores: dict[str, float] = {}
    for query in queries:
        if (
            cfg.max_catalog_search_calls_per_review > 0
            and catalog_search_calls() >= cfg.max_catalog_search_calls_per_review
        ):
            break
        increment_catalog_search_calls()
        hits = await client.search_policy_catalog(
            CatalogSearchRequest(
                tenant_id=tenant_id,
                query=query,
                top_k=cfg.catalog_match_top_k,
            )
        )
        for hit in hits:
            doc_id = str(hit.document_id)
            scores[doc_id] = max(scores.get(doc_id, 0.0), float(hit.score))

    rejected: list[dict[str, str]] = []
    fenced: dict[str, float] = {}
    for doc_id, score in scores.items():
        if allowed and doc_id not in allowed:
            rejected.append({"document_id": doc_id, "reason": "not_in_tenant_registry"})
        else:
            fenced[doc_id] = score

    sorted_ids = sorted(fenced.keys(), key=lambda doc_id: fenced[doc_id], reverse=True)
    capped = sorted_ids[: cfg.catalog_match_max_candidates]
    candidate_scores = {doc_id: fenced[doc_id] for doc_id in capped}
    top_score = max(candidate_scores.values()) if candidate_scores else 0.0

    route_decision = "compare"
    if top_score < cfg.catalog_match_min_score:
        route_decision = "ipc"
    elif 0.60 <= plan.confidence < cfg.routing_compare_min_confidence:
        route_decision = "expand"

    return CatalogMatchResult(
        obligation_id=plan.obligation_id,
        candidate_doc_ids=capped,
        candidate_scores=candidate_scores,
        routing_source="catalog_search",
        confidence=plan.confidence,
        queries_used=queries,
        rejected=rejected,
        route_decision=route_decision,
    )
