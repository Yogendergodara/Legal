"""High-recall multi-path policy retrieval per contract section."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from document_core.config import get_settings as get_core_settings
from document_core.schemas.chunk import DocumentKind, IndexedChunk, RetrievalHit, SearchRequest
from document_core.search.reranker import rerank_hits
from review_agent.clients.document_client import DocumentMCPClient
from review_agent.config import ReviewSettings, get_settings
from review_agent.schemas.section_retrieval import SectionRetrievalBundle
from review_agent.services.section_classifier import classify_section_policies

logger = logging.getLogger(__name__)


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


async def multi_retrieve_for_section(
    client: DocumentMCPClient,
    *,
    tenant_id: str,
    section: IndexedChunk,
    contract_type: str | None,
    policy_type: str | None,
    settings: ReviewSettings | None = None,
) -> SectionRetrievalBundle:
    """Dense + FTS + metadata category retrieval → union → rerank."""
    cfg = settings or get_settings()
    core = get_core_settings()
    recall_k = cfg.retrieval_recall_top_k
    final_k = cfg.retrieval_final_top_k

    classification = await classify_section_policies(
        section,
        contract_type=contract_type,
        settings=cfg,
    )
    query = classification.query_terms[0] if classification.query_terms else section.title
    base = SearchRequest(
        tenant_id=tenant_id,
        query=query,
        kind=DocumentKind.POLICY,
        contract_type=contract_type,
        policy_type=policy_type,
        top_k=recall_k,
    )

    paths: dict[str, Any] = {"categories": classification.categories}

    async def dense_path() -> list[RetrievalHit]:
        hits = await client.search_policy_recall(base)
        paths["dense_count"] = len(hits)
        return hits

    async def fts_path() -> list[RetrievalHit]:
        hits = await client.search_policy_fts(base)
        paths["fts_count"] = len(hits)
        return hits

    async def meta_path() -> list[RetrievalHit]:
        if not classification.categories:
            paths["metadata_count"] = 0
            return []
        hits = await client.search_policy_by_categories(
            base,
            categories=classification.categories,
        )
        paths["metadata_count"] = len(hits)
        return hits

    dense_hits, fts_hits, meta_hits = await asyncio.gather(
        dense_path(),
        fts_path(),
        meta_path(),
    )
    union = _union_hits(dense_hits, fts_hits, meta_hits, paths=paths)
    reranked = rerank_hits(
        query,
        union,
        top_k=final_k,
        enabled=core.reranker_enabled,
    )
    paths["final_count"] = len(reranked)

    return SectionRetrievalBundle(
        section_id=section.section_id,
        categories=classification.categories,
        policy_hits=reranked,
        retrieval_meta=paths,
    )
