"""Hybrid compliance graph nodes (Phase 5)."""

from __future__ import annotations

from typing import Any

from review_agent.clients.document_client import DocumentMCPClient
from review_agent.clients.policy_catalog import get_policy_catalog
from review_agent.config import get_settings
from review_agent.schemas.alignment import AlignmentRecord
from review_agent.schemas.review_category import ReviewCategory
from review_agent.services.alignment import build_alignment_record
from review_agent.services.compliance_batch_llm import run_batched_compliance
from review_agent.services.compliance_merge import merge_compliance_findings
from review_agent.services.compliance_prescreen import run_prescreen
from review_agent.services.gap_retrieval import collect_gap_requests, resolve_gap_hits
from review_agent.graph.nodes import _parse_categories
from review_agent.services.finding_enrich import build_policy_title_map
from review_agent.state.review_state import ReviewState


def _alignment_map(state: ReviewState) -> dict[str, AlignmentRecord]:
    raw = state.get("alignment_by_category") or {}
    return {key: AlignmentRecord.model_validate(value) for key, value in raw.items()}


async def compliance_prescreen_node(state: ReviewState, client: DocumentMCPClient) -> dict[str, Any]:
    _ = client
    categories = _parse_categories(state)
    alignment_by_category = _alignment_map(state)
    outcome = run_prescreen(
        categories,
        state.get("policy_hits_by_category") or {},
        state.get("contract_hits_by_category") or {},
        alignment_by_category,
        state.get("retrieval_meta_by_category") or {},
    )
    return {
        "prescreen_findings": outcome.resolved,
        "deferred_category_ids": [c.category_id for c in outcome.deferred],
    }


async def compliance_review_pass1_node(state: ReviewState, client: DocumentMCPClient) -> dict[str, Any]:
    _ = client
    settings = get_settings()
    categories_by_id = {c.category_id: c for c in _parse_categories(state)}
    deferred_ids = state.get("deferred_category_ids") or []
    deferred = [categories_by_id[cid] for cid in deferred_ids if cid in categories_by_id]

    if not deferred:
        return {"pass1_findings": [], "gap_requests": []}

    alignment = _alignment_map(state)
    title_map = build_policy_title_map(
        state.get("indexed_policies") or [],
        state.get("discovered_policies"),
    )
    pass1_findings, gap_requests = await run_batched_compliance(
        deferred,
        alignment_by_category=alignment,
        policy_hits_by_category=state.get("policy_hits_by_category") or {},
        contract_hits_by_category=state.get("contract_hits_by_category") or {},
        retrieval_meta_by_category=state.get("retrieval_meta_by_category") or {},
        memory_context=state.get("memory_context") or "",
        compliance_pass="pass1",
        settings=settings,
        policy_titles_by_doc=title_map,
        contract_type=state.get("contract_type"),
    )
    return {"pass1_findings": pass1_findings, "gap_requests": gap_requests}


async def policy_gap_retrieval_node(state: ReviewState, client: DocumentMCPClient) -> dict[str, Any]:
    settings = get_settings()
    raw_gaps = state.get("gap_requests") or []
    if not settings.compliance_gap_pass_enabled or not raw_gaps:
        return {"gap_hits_by_request": {}}

    gaps = collect_gap_requests(raw_gaps)
    categories_by_id = {c.category_id: c for c in _parse_categories(state)}
    catalog = get_policy_catalog(
        catalog_url=settings.policy_catalog_url,
        fetch_enabled=settings.policy_fetch_enabled,
    )
    fetched_refs: set[str] = set(state.get("fetched_policy_refs") or [])
    ref_by_doc: dict[str, str] = dict(state.get("policy_ref_by_document_id") or {})

    gap_hits = await resolve_gap_hits(
        client,
        tenant_id=state["tenant_id"],
        gaps=gaps,
        categories_by_id=categories_by_id,
        contract_document_id=state["ingest_result"].document_id,
        contract_type=state.get("contract_type"),
        policy_type=state.get("policy_type"),
        policy_document_ids=state.get("policy_document_ids"),
        fetched_refs=fetched_refs,
        policy_ref_by_doc=ref_by_doc,
        catalog=catalog,
        settings=settings,
    )
    return {
        "gap_hits_by_request": gap_hits,
        "fetched_policy_refs": sorted(fetched_refs),
        "policy_ref_by_document_id": ref_by_doc,
    }


async def compliance_review_pass2_node(state: ReviewState, client: DocumentMCPClient) -> dict[str, Any]:
    _ = client
    settings = get_settings()
    if not settings.compliance_gap_pass_enabled:
        return {"pass2_findings": []}

    gaps = collect_gap_requests(state.get("gap_requests") or [])
    gap_hits = state.get("gap_hits_by_request") or {}
    if not gaps or not gap_hits:
        return {"pass2_findings": []}

    categories_by_id = {c.category_id: c for c in _parse_categories(state)}
    policy_hits = dict(state.get("policy_hits_by_category") or {})
    contract_hits = dict(state.get("contract_hits_by_category") or {})
    alignment_raw = dict(state.get("alignment_by_category") or {})
    retrieval_meta = dict(state.get("retrieval_meta_by_category") or {})

    pass2_categories: list[ReviewCategory] = []
    for gap in gaps:
        hits = gap_hits.get(gap.request_id) or []
        if not hits or not gap.category_id:
            continue
        category = categories_by_id.get(gap.category_id)
        if category is None:
            continue
        policy_hits[gap.category_id] = hits
        record = build_alignment_record(
            category,
            hits,
            contract_hits.get(gap.category_id, []),
            retrieval_meta.get(gap.category_id, {}),
            settings=settings,
        )
        alignment_raw[gap.category_id] = record.model_dump(mode="json")
        pass2_categories.append(category)

    if not pass2_categories:
        return {"pass2_findings": []}

    alignment = {k: AlignmentRecord.model_validate(v) for k, v in alignment_raw.items()}
    title_map = build_policy_title_map(
        state.get("indexed_policies") or [],
        state.get("discovered_policies"),
    )
    pass2_findings, _extra_gaps = await run_batched_compliance(
        pass2_categories,
        alignment_by_category=alignment,
        policy_hits_by_category=policy_hits,
        contract_hits_by_category=contract_hits,
        retrieval_meta_by_category=retrieval_meta,
        memory_context=state.get("memory_context") or "",
        compliance_pass="pass2",
        settings=settings,
        policy_titles_by_doc=title_map,
        contract_type=state.get("contract_type"),
    )
    return {"pass2_findings": pass2_findings}


async def compliance_hybrid_merge_node(state: ReviewState, client: DocumentMCPClient) -> dict[str, Any]:
    _ = client
    merged = merge_compliance_findings(
        prescreen=state.get("prescreen_findings") or [],
        pass1=state.get("pass1_findings") or [],
        pass2=state.get("pass2_findings") or [],
    )
    stats = {
        "compliance_mode": "hybrid",
        "prescreen_count": len(state.get("prescreen_findings") or []),
        "pass1_count": len(state.get("pass1_findings") or []),
        "pass2_count": len(state.get("pass2_findings") or []),
        "gap_count": len(state.get("gap_requests") or []),
        "finding_count": len(merged),
    }
    return {"findings": merged, "compliance_stats": stats}
