"""Scoped obligation retrieval inside R3 candidate fence (Phase R4)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from document_core.config import get_settings as get_core_settings
from document_core.schemas.chunk import RetrievalHit
from review_agent.clients.document_client import DocumentMCPClient
from review_agent.config import ReviewSettings, get_settings
from review_agent.schemas.obligation import ContractObligation
from review_agent.schemas.obligation_retrieval import ObligationRetrievalBundle
from review_agent.schemas.routing_plan import CatalogMatchResult, ObligationRoutingPlan
from review_agent.services.multi_retrieval import retrieve_hybrid_attempt
from review_agent.services.policy_coverage import catalog_doc_categories
from review_agent.services.retrieval_relevance import filter_hits_by_relevance


def _unique_queries(*parts: list[str] | str | None, cap: int) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for part in parts:
        if isinstance(part, str):
            items = [part]
        else:
            items = list(part or [])
        for item in items:
            query = str(item).strip()
            if not query or query in seen:
                continue
            seen.add(query)
            ordered.append(query)
            if len(ordered) >= cap:
                return ordered
    return ordered


def _union_hits(*hit_lists: list[RetrievalHit]) -> list[RetrievalHit]:
    merged: dict[str, RetrievalHit] = {}
    for hits in hit_lists:
        for hit in hits:
            key = hit.parent_chunk.chunk_id
            existing = merged.get(key)
            if existing is None or hit.score > existing.score:
                merged[key] = hit
    return sorted(merged.values(), key=lambda h: h.score, reverse=True)


def _diverse_top_k(
    hits: list[RetrievalHit],
    top_k: int,
    *,
    max_per_document: int,
) -> list[RetrievalHit]:
    doc_count: dict[str, int] = {}
    selected: list[RetrievalHit] = []
    for hit in hits:
        doc_id = str(hit.parent_chunk.document_id)
        if doc_count.get(doc_id, 0) >= max_per_document:
            continue
        doc_count[doc_id] = doc_count.get(doc_id, 0) + 1
        selected.append(hit)
        if len(selected) >= top_k:
            break
    return selected


async def retrieve_for_obligation(
    client: DocumentMCPClient,
    *,
    obligation: ContractObligation,
    plan: ObligationRoutingPlan,
    match: CatalogMatchResult,
    tenant_id: str,
    contract_type: str | None,
    policy_type: str | None,
    settings: ReviewSettings | None = None,
    policy_catalog: list[dict] | None = None,
    expand_mode: bool = False,
    extra_doc_ids: list[str] | None = None,
    extra_queries: list[str] | None = None,
) -> ObligationRetrievalBundle:
    cfg = settings or get_settings()
    core = get_core_settings()

    if match.route_decision == "ipc" or plan.routing_source == "skipped_boilerplate":
        return ObligationRetrievalBundle(
            obligation_id=obligation.obligation_id,
            section_id=obligation.section_id,
            concepts=list(plan.concepts),
            skipped_reason="ipc_preflight",
        )

    candidate_doc_ids = list(
        dict.fromkeys(
            [doc_id for doc_id in match.candidate_doc_ids if str(doc_id).strip()]
            + [doc_id for doc_id in (extra_doc_ids or []) if str(doc_id).strip()]
        )
    )
    if not candidate_doc_ids:
        return ObligationRetrievalBundle(
            obligation_id=obligation.obligation_id,
            section_id=obligation.section_id,
            concepts=list(plan.concepts),
            skipped_reason="empty_fence",
        )

    base_queries = plan.search_queries or [plan.intent or (obligation.text or "")[:200]]
    expand_queries: list[str] = []
    if expand_mode:
        expand_queries = list(plan.concepts)
        if obligation.obligation_type and obligation.obligation_type != "general":
            expand_queries.append(obligation.obligation_type)
    queries = _unique_queries(
        extra_queries,
        expand_queries,
        base_queries,
        cap=cfg.obligation_retrieval_max_queries,
    )

    filter_doc_ids = [UUID(doc_id) for doc_id in candidate_doc_ids]
    query_steps: list[dict[str, Any]] = []
    per_query_hits: list[list[RetrievalHit]] = []

    for index, query in enumerate(queries):
        hits, step = await retrieve_hybrid_attempt(
            client,
            tenant_id=tenant_id,
            query=query,
            categories=[],
            contract_type=contract_type,
            policy_type=policy_type,
            filter_doc_ids=filter_doc_ids,
            category_hard_filter=False,
            attempt_index=index,
            cfg=cfg,
            core=core,
        )
        per_query_hits.append(hits)
        query_steps.append(step)

    hits = _union_hits(*per_query_hits)
    hits = _diverse_top_k(
        hits,
        top_k=cfg.obligation_retrieval_union_top_k,
        max_per_document=cfg.retrieval_max_hits_per_document,
    )[: cfg.retrieval_final_top_k]

    relevance_dropped = 0
    if hits and cfg.retrieval_relevance_gate_enabled:
        relevance_floor = max(
            cfg.retrieval_relevance_min_score,
            cfg.compare_hit_min_relevance_score,
        )
        catalog_cats = catalog_doc_categories(policy_catalog or [])
        relevant, dropped = filter_hits_by_relevance(
            hits,
            section_categories=list(plan.concepts),
            section_title=(obligation.text or "")[:120],
            min_score=relevance_floor,
            keep_best_fallback=cfg.retrieval_relevance_keep_best_fallback,
            doc_catalog_categories=catalog_cats or None,
        )
        if relevant:
            relevance_dropped = len(dropped)
            hits = relevant

    meta: dict[str, Any] = {
        "fence_document_count": len(candidate_doc_ids),
        "query_steps": query_steps,
        "union_count": len(hits),
        "expand_mode": expand_mode,
    }
    if relevance_dropped:
        meta["relevance_dropped"] = relevance_dropped

    return ObligationRetrievalBundle(
        obligation_id=obligation.obligation_id,
        section_id=obligation.section_id,
        candidate_doc_ids=candidate_doc_ids,
        policy_hits=hits,
        queries_used=queries,
        concepts=list(plan.concepts),
        retrieval_meta=meta,
    )
