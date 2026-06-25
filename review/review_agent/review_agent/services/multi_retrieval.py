"""High-recall multi-path policy retrieval per contract section."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

from document_core.config import get_settings as get_core_settings
from document_core.schemas.chunk import DocumentKind, IndexedChunk, RetrievalHit, SearchRequest
from document_core.schemas.taxonomy import normalize_categories
from document_core.search.reranker import rerank_hits
from document_core.services.search import count_parent_category_hits
from review_agent.clients.document_client import DocumentMCPClient
from review_agent.config import ReviewSettings, get_settings
from review_agent.schemas.section_classify import SectionCategoryResult
from review_agent.schemas.section_retrieval import SectionRetrievalBundle
from review_agent.services.named_policy_routing import (
    extract_named_policy_title_keys,
    resolve_named_policy_doc_ids,
)
from review_agent.services.policy_coverage import (
    catalog_doc_categories,
    filter_doc_ids_by_category_overlap,
)
from review_agent.services.retrieval_relevance import filter_hits_by_relevance
from review_agent.services.section_classifier import classify_section_policies

logger = logging.getLogger(__name__)


def _normalize_path_scores(hits: list[RetrievalHit]) -> list[RetrievalHit]:
    """Min-max normalize scores within a single retrieval path.

    Skips normalization for single-hit lists (no range to scale).
    """
    if len(hits) < 2:
        return hits
    scores = [h.score for h in hits]
    lo, hi = min(scores), max(scores)
    span = hi - lo or 1.0
    return [
        h.model_copy(update={"score": (h.score - lo) / span})
        for h in hits
    ]


def _union_hits(
    *hit_lists: list[RetrievalHit],
    paths: dict[str, int],
) -> list[RetrievalHit]:
    merged: dict[str, RetrievalHit] = {}
    for hits in hit_lists:
        for hit in hits:
            key = hit.parent_chunk.chunk_id
            existing = merged.get(key)
            if existing is None or hit.score > existing.score:
                merged[key] = hit
            elif hit.score == existing.score:
                for cid in hit.matched_child_ids:
                    if cid not in existing.matched_child_ids:
                        existing.matched_child_ids.append(cid)
    ordered = sorted(merged.values(), key=lambda h: h.score, reverse=True)
    paths["union_count"] = len(ordered)
    return ordered


def _diverse_top_k(
    hits: list[RetrievalHit],
    top_k: int,
    *,
    max_per_document: int = 3,
) -> list[RetrievalHit]:
    """Cap hits per document_id to enforce diversity before rerank."""
    doc_count: dict[str, int] = {}
    selected: list[RetrievalHit] = []
    for hit in sorted(hits, key=lambda h: h.score, reverse=True):
        doc_id = str(hit.parent_chunk.document_id)
        if doc_count.get(doc_id, 0) >= max_per_document:
            continue
        doc_count[doc_id] = doc_count.get(doc_id, 0) + 1
        selected.append(hit)
        if len(selected) >= top_k:
            break
    return selected


def _parse_scope_ids(scope_document_ids: list[str] | None) -> set[str]:
    return {str(item).strip() for item in (scope_document_ids or []) if str(item).strip()}


def _is_general_only(categories: list[str]) -> bool:
    normalized = normalize_categories(categories)
    return not normalized or normalized == ["general"]


def _specific_categories_for_overlap(categories: list[str]) -> set[str]:
    from review_agent.services.retrieval_relevance import _specific_categories

    return _specific_categories(categories)


def _query_for_attempt(
    classification: SectionCategoryResult,
    section: IndexedChunk,
    attempt: int,
    *,
    contract_routing: dict[str, Any] | None = None,
) -> tuple[str, list[str], bool]:
    terms = classification.query_terms or []
    title = (section.title or section.section_id or "").strip()

    if attempt == 0:
        query = terms[0] if terms else title
        if _is_general_only(classification.categories) and contract_routing:
            topics = contract_routing.get("topics") or []
            title_lower = title.lower()
            for topic in topics:
                if isinstance(topic, str):
                    label = topic.strip()
                elif isinstance(topic, dict):
                    label = str(topic.get("label") or topic.get("topic") or "").strip()
                else:
                    label = str(topic).strip()
                if label and label.lower() in title_lower:
                    query = label
                    break
        return query, list(classification.categories), True
    if attempt == 1:
        if len(terms) > 1:
            return terms[1], list(classification.categories), True
        if title:
            return title, list(classification.categories), True
        fallback = terms[0] if terms else title
        return " ".join(fallback.split()[:3]), list(classification.categories), True

    query = title or (terms[0] if terms else (section.section_id or ""))
    categories = list(classification.categories)
    if "general" not in categories:
        categories.append("general")
    return query, categories, False


async def _resolve_filter_document_ids(
    client: DocumentMCPClient,
    *,
    tenant_id: str,
    categories: list[str],
    contract_type: str | None,
    scope_document_ids: list[str] | None,
    category_hard_filter: bool,
    cfg: ReviewSettings,
    policy_catalog: list[dict] | None = None,
) -> tuple[list[UUID] | None, dict[str, Any]]:
    filter_meta: dict[str, Any] = {"category_hard_filter": category_hard_filter}
    scope_set = _parse_scope_ids(scope_document_ids)
    if scope_set:
        filter_meta["scope_document_ids"] = sorted(scope_set)

    category_ids: list[UUID] = []
    if category_hard_filter and categories:
        category_ids = await client.list_policy_ids_by_categories(
            tenant_id,
            categories,
            contract_type=contract_type,
        )
        filter_meta["category_filter_document_ids"] = [str(doc_id) for doc_id in category_ids]

    if category_hard_filter and categories and not category_ids:
        if cfg.retrieval_category_filter_fallback:
            filter_meta["category_filter_skipped"] = "no category matches"
            if scope_set:
                return [UUID(doc_id) for doc_id in scope_set], filter_meta
            return None, filter_meta
        return [], filter_meta

    doc_ids = list(category_ids)
    if doc_ids and policy_catalog:
        catalog_cats = catalog_doc_categories(policy_catalog)
        specific = _specific_categories_for_overlap(categories)
        min_overlap = cfg.retrieval_category_min_overlap
        if min_overlap <= 0:
            min_overlap = 2 if len(specific) >= 2 else 1
        if specific:
            doc_ids = filter_doc_ids_by_category_overlap(
                doc_ids,
                section_categories=categories,
                catalog_categories=catalog_cats,
                min_overlap=min_overlap,
            )
            filter_meta["category_overlap_min"] = min_overlap
            filter_meta["category_overlap_doc_count"] = len(doc_ids)

    if scope_set:
        if doc_ids:
            doc_ids = [doc_id for doc_id in doc_ids if str(doc_id) in scope_set]
            if not doc_ids and cfg.retrieval_category_filter_fallback:
                filter_meta["category_filter_skipped"] = "scope intersection empty"
                doc_ids = [UUID(doc_id) for doc_id in scope_set]
        else:
            doc_ids = [UUID(doc_id) for doc_id in scope_set]

    return doc_ids or None, filter_meta


async def _retrieve_attempt(
    client: DocumentMCPClient,
    *,
    tenant_id: str,
    query: str,
    categories: list[str],
    contract_type: str | None,
    policy_type: str | None,
    filter_doc_ids: list[UUID] | None,
    category_hard_filter: bool,
    attempt_index: int,
    cfg: ReviewSettings,
    core,
) -> tuple[list[RetrievalHit], dict[str, Any]]:
    recall_k = cfg.retrieval_recall_top_k
    if attempt_index == 1 and cfg.retrieval_broaden_on_retry:
        recall_k = min(int(recall_k * 1.5), 50)

    request_kwargs: dict[str, Any] = {
        "tenant_id": tenant_id,
        "query": query,
        "kind": DocumentKind.POLICY,
        "contract_type": contract_type,
        "policy_type": policy_type,
        "top_k": recall_k,
    }
    if filter_doc_ids is not None:
        request_kwargs["document_ids"] = filter_doc_ids
    base = SearchRequest(**request_kwargs)

    step: dict[str, Any] = {
        "attempt": attempt_index,
        "query": query,
        "category_hard_filter": category_hard_filter,
        "filter_document_count": len(filter_doc_ids or []),
    }

    async def dense_path() -> list[RetrievalHit]:
        hits = await client.search_policy_recall(base)
        step["dense_count"] = len(hits)
        return hits

    async def fts_path() -> list[RetrievalHit]:
        hits = await client.search_policy_fts(base)
        step["fts_count"] = len(hits)
        return hits

    async def meta_path() -> list[RetrievalHit]:
        if not categories:
            step["metadata_count"] = 0
            step["parent_category_hits"] = 0
            return []
        hits = await client.search_policy_by_categories(base, categories=categories)
        step["metadata_count"] = len(hits)
        step["parent_category_hits"] = count_parent_category_hits(hits, categories)
        return hits

    dense_hits, fts_hits, meta_hits = await asyncio.gather(
        dense_path(),
        fts_path(),
        meta_path(),
    )
    union = _union_hits(
        _normalize_path_scores(dense_hits),
        _normalize_path_scores(fts_hits),
        _normalize_path_scores(meta_hits),
        paths=step,
    )
    diverse = _diverse_top_k(
        union,
        top_k=cfg.retrieval_final_top_k * 2,
        max_per_document=cfg.retrieval_max_hits_per_document,
    )
    step["diverse_count"] = len(diverse)
    rerank_usage: dict[str, str] = {}
    reranked = rerank_hits(
        query,
        diverse,
        top_k=cfg.retrieval_final_top_k,
        enabled=core.reranker_enabled,
        backend=core.reranker_backend,
        max_passage_chars=core.reranker_max_passage_chars,
        fusion_retrieval_weight=core.reranker_fusion_retrieval_weight,
        usage=rerank_usage,
    )
    step["reranker_backend"] = core.reranker_backend if core.reranker_enabled else "off"
    if rerank_usage.get("reranker_used"):
        step["reranker_used"] = rerank_usage["reranker_used"]
    step["final_count"] = len(reranked)
    return reranked, step


async def multi_retrieve_for_section(
    client: DocumentMCPClient,
    *,
    tenant_id: str,
    section: IndexedChunk,
    contract_type: str | None,
    policy_type: str | None,
    settings: ReviewSettings | None = None,
    classification: SectionCategoryResult | None = None,
    scope_document_ids: list[str] | None = None,
    contract_routing: dict[str, Any] | None = None,
    policy_catalog: list[dict] | None = None,
) -> SectionRetrievalBundle:
    """Dense + FTS + metadata retrieval with retry ladder and category filtering."""
    cfg = settings or get_settings()
    core = get_core_settings()

    if classification is None:
        classification = await classify_section_policies(
            section,
            contract_type=contract_type,
            settings=cfg,
        )

    attempts_meta: list[dict[str, Any]] = []
    hits: list[RetrievalHit] = []
    winning_step: dict[str, Any] = {}
    filter_meta: dict[str, Any] = {}
    max_attempts = max(1, cfg.retrieval_max_attempts)

    for attempt_index in range(max_attempts):
        query, categories, wants_category_filter = _query_for_attempt(
            classification,
            section,
            attempt_index,
            contract_routing=contract_routing,
        )
        use_category_filter = wants_category_filter and cfg.retrieval_category_hard_filter
        if cfg.retrieval_skip_hard_filter_for_general and _is_general_only(
            classification.categories
        ):
            use_category_filter = False

        named_doc_ids: list[str] = []
        if (
            attempt_index == 0
            and cfg.named_policy_routing_enabled
            and policy_catalog
        ):
            keys = extract_named_policy_title_keys(section.text or "")
            named_doc_ids = resolve_named_policy_doc_ids(keys, policy_catalog)
            if named_doc_ids:
                filter_meta["named_policy_keys"] = keys
                filter_meta["named_policy_doc_ids"] = named_doc_ids

        filter_doc_ids, resolve_meta = await _resolve_filter_document_ids(
            client,
            tenant_id=tenant_id,
            categories=categories if use_category_filter else [],
            contract_type=contract_type,
            scope_document_ids=scope_document_ids,
            category_hard_filter=use_category_filter,
            cfg=cfg,
            policy_catalog=policy_catalog,
        )
        if attempt_index == 0:
            filter_meta = {**filter_meta, **resolve_meta}
        elif not filter_meta:
            filter_meta = resolve_meta

        if named_doc_ids and attempt_index == 0:
            scope_set = _parse_scope_ids(scope_document_ids)
            routed = [
                UUID(doc_id)
                for doc_id in named_doc_ids
                if not scope_set or doc_id in scope_set
            ]
            if routed:
                if filter_doc_ids is None:
                    filter_doc_ids = routed
                else:
                    filter_doc_ids = [doc_id for doc_id in filter_doc_ids if doc_id in routed] or routed
                filter_meta["named_policy_routing_applied"] = True

        if use_category_filter and filter_doc_ids is not None and not filter_doc_ids:
            step = {
                "attempt": attempt_index,
                "query": query,
                "category_hard_filter": True,
                "filter_document_count": 0,
                "dense_count": 0,
                "fts_count": 0,
                "metadata_count": 0,
                "union_count": 0,
                "final_count": 0,
            }
            attempts_meta.append(step)
            winning_step = step
            continue

        hits, step = await _retrieve_attempt(
            client,
            tenant_id=tenant_id,
            query=query,
            categories=categories,
            contract_type=contract_type,
            policy_type=policy_type,
            filter_doc_ids=filter_doc_ids,
            category_hard_filter=use_category_filter,
            attempt_index=attempt_index,
            cfg=cfg,
            core=core,
        )
        attempts_meta.append(step)
        winning_step = step
        if step["final_count"] > 0:
            break

    relevance_dropped = 0
    if hits and cfg.retrieval_relevance_gate_enabled:
        relevant, dropped = filter_hits_by_relevance(
            hits,
            section_categories=classification.categories,
            section_title=section.title or section.section_id,
            min_score=cfg.retrieval_relevance_min_score,
        )
        if relevant:
            relevance_dropped = len(dropped)
            hits = relevant

    paths: dict[str, Any] = {
        "categories": classification.categories,
        "query_terms": classification.query_terms,
        **filter_meta,
        "attempts": attempts_meta,
        "final_attempt": winning_step.get("attempt", 0),
        "final_count": len(hits),
        "dense_count": winning_step.get("dense_count", 0),
        "fts_count": winning_step.get("fts_count", 0),
        "metadata_count": winning_step.get("metadata_count", 0),
        "union_count": winning_step.get("union_count", 0),
    }
    if relevance_dropped:
        paths["relevance_dropped"] = relevance_dropped
    if winning_step.get("reranker_used"):
        paths["reranker_used"] = winning_step["reranker_used"]
    if winning_step.get("reranker_backend"):
        paths["reranker_backend"] = winning_step["reranker_backend"]
    if classification.classify_warning:
        paths["classify_warning"] = classification.classify_warning

    return SectionRetrievalBundle(
        section_id=section.section_id,
        categories=classification.categories,
        policy_hits=hits,
        retrieval_meta=paths,
    )
