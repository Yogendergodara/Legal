"""Scoped obligation retrieval inside R3 candidate fence (Phase R4)."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from document_core.config import get_settings as get_core_settings
from document_core.schemas.chunk import IndexedChunk, RetrievalHit
from review_agent.clients.document_client import DocumentMCPClient
from review_agent.config import ReviewSettings, get_settings
from review_agent.schemas.obligation import ContractObligation
from review_agent.schemas.obligation_retrieval import ObligationRetrievalBundle
from review_agent.schemas.routing_plan import CatalogMatchResult, ObligationRoutingPlan
from review_agent.schemas.section_retrieval import SectionRetrievalBundle
from review_agent.services.ipc3_gates import boilerplate_substantive_override
from review_agent.services.obligation_relevance import obligation_relevance_categories
from review_agent.services.multi_retrieval import retrieve_hybrid_attempt
from review_agent.services.pipeline_mode import parallel_pipeline_active
from review_agent.services.policy_coverage import catalog_doc_categories
from review_agent.services.retrieval_relevance import filter_hits_by_relevance, relevance_filter_kwargs


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


def _max_hit_score(hits: list[RetrievalHit]) -> float:
    if not hits:
        return 0.0
    return max(hit.score for hit in hits)


def _retrieval_hits_sufficient(hits: list[RetrievalHit], cfg: ReviewSettings) -> bool:
    """Align with evidence_sufficiency hit-count/score gates (PF-1B ladder early-exit)."""
    if len(hits) < cfg.evidence_min_hits:
        return False
    return _max_hit_score(hits) >= cfg.evidence_min_score


def _union_and_cap_hits(
    per_query_hits: list[list[RetrievalHit]],
    cfg: ReviewSettings,
) -> list[RetrievalHit]:
    hits = _union_hits(*per_query_hits)
    hits = _diverse_top_k(
        hits,
        top_k=cfg.obligation_retrieval_union_top_k,
        max_per_document=cfg.retrieval_max_hits_per_document,
    )
    return hits[: cfg.retrieval_final_top_k]


def _apply_relevance_filter(
    hits: list[RetrievalHit],
    *,
    obligation: ContractObligation,
    plan: ObligationRoutingPlan,
    section: IndexedChunk | None,
    policy_catalog: list[dict] | None,
    cfg: ReviewSettings,
) -> tuple[list[RetrievalHit], dict[str, Any]]:
    relevance_meta: dict[str, Any] = {}
    if not hits or not cfg.retrieval_relevance_gate_enabled:
        return hits, relevance_meta

    section_title = (
        (section.title or "")[:120] if section else (obligation.text or "")[:120]
    )
    categories, cat_source = obligation_relevance_categories(
        plan=plan,
        obligation=obligation,
        section=section,
        settings=cfg,
    )
    catalog_cats = catalog_doc_categories(policy_catalog or [])
    if cfg.retrieval_coverage_filter_aligned:
        kw = relevance_filter_kwargs(cfg, stage="retrieval")
        kw["keep_best_fallback"] = cfg.obligation_retrieval_keep_best_fallback
        if cat_source == "general_fallback":
            kw["require_specific_overlap"] = False
    else:
        relevance_floor = max(
            cfg.retrieval_relevance_min_score,
            cfg.compare_hit_min_relevance_score,
        )
        kw = {
            "min_score": relevance_floor,
            "keep_best_fallback": cfg.obligation_retrieval_keep_best_fallback,
            "require_specific_overlap": False,
        }
    if cfg.obligation_relevance_fallback_on_overlap_miss:
        kw["fallback_on_overlap_miss"] = True
    relevant, dropped = filter_hits_by_relevance(
        hits,
        section_categories=categories,
        section_title=section_title,
        doc_catalog_categories=catalog_cats or None,
        **kw,
    )
    if relevant:
        relevance_meta["relevance_dropped"] = len(dropped)
        hits = relevant
    if cfg.retrieval_coverage_filter_aligned and hits:
        relevance_meta["relevance_gate_applied"] = True
    if categories:
        relevance_meta["relevance_categories"] = categories
        relevance_meta["relevance_category_source"] = cat_source
    return hits, relevance_meta


def _seed_hits_from_section_bundle(
    section_bundle: SectionRetrievalBundle,
    fence_doc_ids: list[str],
) -> list[RetrievalHit]:
    fence = {str(doc_id).strip() for doc_id in fence_doc_ids if str(doc_id).strip()}
    if not fence:
        return []
    return [
        hit
        for hit in section_bundle.policy_hits
        if str(hit.parent_chunk.document_id) in fence
    ]


def should_skip_obligation_for_resolved_section(
    *,
    tenant_id: str,
    plan: ObligationRoutingPlan,
    match: CatalogMatchResult,
    section_bundle: SectionRetrievalBundle | None,
    settings: ReviewSettings,
) -> bool:
    """PF-1C-2: skip obligation MCP when section path already has strong policy hits."""
    if not settings.obligation_retrieval_skip_resolved_sections:
        return False
    # OB-01: parallel_hybrid runs obligation retrieval before section compare — hits ≠ resolved.
    if (
        settings.obligation_skip_resolved_parallel_guard
        and tenant_id
        and parallel_pipeline_active(tenant_id, settings)
    ):
        return False
    if plan.routing_source == "skipped_boilerplate":
        return False
    if (
        match.route_decision == "compare"
        and match.confidence >= settings.routing_compare_min_confidence
    ):
        return False
    if section_bundle is None or not section_bundle.policy_hits:
        return False
    meta = section_bundle.retrieval_meta or {}
    if meta.get("skipped_reason") == "boilerplate" or meta.get("substantive") is False:
        return False
    if meta.get("coverage_gate_ipc") or meta.get("incompatible_family"):
        return False
    if len(section_bundle.policy_hits) < settings.evidence_min_hits:
        return False
    return _max_hit_score(section_bundle.policy_hits) >= settings.evidence_min_score


async def _run_obligation_queries(
    client: DocumentMCPClient,
    *,
    queries: list[str],
    tenant_id: str,
    contract_type: str | None,
    policy_type: str | None,
    filter_doc_ids: list[UUID],
    cfg: ReviewSettings,
    core,
) -> tuple[list[list[RetrievalHit]], list[dict[str, Any]], list[str], dict[str, Any]]:
    if not queries:
        return [], [], [], {}

    per_query_hits: list[list[RetrievalHit]] = []
    query_steps: list[dict[str, Any]] = []
    executed: list[str] = []
    run_meta: dict[str, Any] = {
        "queries_planned": len(queries),
        "queries_executed": 0,
        "ladder_early_exit": False,
        "parallel_query_batch": False,
    }

    async def _one(index: int, query: str) -> tuple[list[RetrievalHit], dict[str, Any]]:
        return await retrieve_hybrid_attempt(
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

    async def _append(index: int, query: str) -> None:
        hits, step = await _one(index, query)
        per_query_hits.append(hits)
        query_steps.append(step)
        executed.append(query)

    if cfg.obligation_retrieval_adaptive_ladder:
        await _append(0, queries[0])
        merged = _union_and_cap_hits(per_query_hits, cfg)
        if len(queries) == 1 or _retrieval_hits_sufficient(merged, cfg):
            if len(queries) > 1 and _retrieval_hits_sufficient(merged, cfg):
                run_meta["ladder_early_exit"] = True
            run_meta["queries_executed"] = len(executed)
            return per_query_hits, query_steps, executed, run_meta

        remaining = list(enumerate(queries[1:], start=1))
        if cfg.obligation_retrieval_parallel_queries and len(remaining) > 1:
            run_meta["parallel_query_batch"] = True
            results = await asyncio.gather(*[_one(i, q) for i, q in remaining])
            for (i, q), (hits, step) in zip(remaining, results, strict=True):
                per_query_hits.append(hits)
                query_steps.append(step)
                executed.append(q)
        else:
            for i, q in remaining:
                await _append(i, q)
    elif cfg.obligation_retrieval_parallel_queries and len(queries) > 1:
        run_meta["parallel_query_batch"] = True
        results = await asyncio.gather(*[_one(i, q) for i, q in enumerate(queries)])
        for (i, q), (hits, step) in zip(enumerate(queries), results, strict=True):
            per_query_hits.append(hits)
            query_steps.append(step)
            executed.append(q)
    else:
        for index, query in enumerate(queries):
            await _append(index, query)

    run_meta["queries_executed"] = len(executed)
    return per_query_hits, query_steps, executed, run_meta


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
    section: IndexedChunk | None = None,
    section_bundle: SectionRetrievalBundle | None = None,
) -> ObligationRetrievalBundle:
    cfg = settings or get_settings()
    core = get_core_settings()

    if plan.routing_source == "skipped_boilerplate":
        if not boilerplate_substantive_override(obligation, plan, cfg):
            return ObligationRetrievalBundle(
                obligation_id=obligation.obligation_id,
                section_id=obligation.section_id,
                concepts=list(plan.concepts),
                skipped_reason="boilerplate",
            )

    skip_preflight = (
        match.route_decision == "ipc"
        and (
            not cfg.evidence_compare_on_catalog_candidates
            or not match.candidate_doc_ids
        )
    )
    if skip_preflight:
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
    seeded_hits: list[RetrievalHit] = []
    seeded_accepted = False
    reuse_meta: dict[str, Any] = {}
    if cfg.obligation_retrieval_section_hit_reuse and section_bundle is not None:
        seeded_hits = _seed_hits_from_section_bundle(section_bundle, candidate_doc_ids)
        if seeded_hits:
            reuse_meta["section_hit_reuse"] = True
            reuse_meta["section_hit_reuse_count"] = len(seeded_hits)
            seeded_capped = _union_and_cap_hits([seeded_hits], cfg)
            if _retrieval_hits_sufficient(seeded_capped, cfg):
                hits, relevance_meta = _apply_relevance_filter(
                    seeded_capped,
                    obligation=obligation,
                    plan=plan,
                    section=section,
                    policy_catalog=policy_catalog,
                    cfg=cfg,
                )
                if _retrieval_hits_sufficient(hits, cfg):
                    seeded_accepted = True
                    meta = {
                        "fence_document_count": len(candidate_doc_ids),
                        "query_steps": [],
                        "union_count": len(hits),
                        "expand_mode": expand_mode,
                        "queries_planned": 0,
                        "queries_executed": 0,
                        "ladder_early_exit": False,
                        "parallel_query_batch": False,
                        **reuse_meta,
                        **relevance_meta,
                    }
                    return ObligationRetrievalBundle(
                        obligation_id=obligation.obligation_id,
                        section_id=obligation.section_id,
                        candidate_doc_ids=candidate_doc_ids,
                        policy_hits=hits,
                        queries_used=[],
                        concepts=list(plan.concepts),
                        retrieval_meta=meta,
                    )

    per_query_hits, query_steps, executed_queries, run_meta = await _run_obligation_queries(
        client,
        queries=queries,
        tenant_id=tenant_id,
        contract_type=contract_type,
        policy_type=policy_type,
        filter_doc_ids=filter_doc_ids,
        cfg=cfg,
        core=core,
    )

    hits = _union_and_cap_hits(per_query_hits, cfg)
    if seeded_hits and not seeded_accepted:
        reuse_meta["section_hit_reuse_rejected"] = True
    hits, relevance_meta = _apply_relevance_filter(
        hits,
        obligation=obligation,
        plan=plan,
        section=section,
        policy_catalog=policy_catalog,
        cfg=cfg,
    )

    meta: dict[str, Any] = {
        "fence_document_count": len(candidate_doc_ids),
        "query_steps": query_steps,
        "union_count": len(hits),
        "expand_mode": expand_mode,
        **run_meta,
        **reuse_meta,
        **relevance_meta,
    }

    return ObligationRetrievalBundle(
        obligation_id=obligation.obligation_id,
        section_id=obligation.section_id,
        candidate_doc_ids=candidate_doc_ids,
        policy_hits=hits,
        queries_used=executed_queries,
        concepts=list(plan.concepts),
        retrieval_meta=meta,
    )
