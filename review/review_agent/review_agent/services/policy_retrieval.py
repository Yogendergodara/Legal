"""Policy and contract retrieval with exact lookup, search retry, and catalog fetch."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

from document_core.schemas.chunk import (
    DocumentKind,
    GetSectionRequest,
    IndexedChunk,
    RetrievalHit,
    SearchRequest,
)

from review_agent.clients.document_client import DocumentMCPClient
from review_agent.clients.policy_catalog import (
    PolicyCatalogClient,
    index_fetched_policy,
)
from review_agent.config import ReviewSettings
from review_agent.schemas.review_category import ReviewCategory
from review_agent.services.async_limits import gather_limited

logger = logging.getLogger(__name__)


def _hit_from_section(section: IndexedChunk) -> RetrievalHit:
    return RetrievalHit(parent_chunk=section, score=1.0)


def _primary_query(category: ReviewCategory) -> str:
    if category.search_queries:
        return category.search_queries[0]
    return category.label


async def _search_contract_hits(
    client: DocumentMCPClient,
    *,
    tenant_id: str,
    contract_document_id: UUID,
    category: ReviewCategory,
    settings: ReviewSettings,
) -> tuple[list[RetrievalHit], dict[str, Any]]:
    primary = _primary_query(category)
    queries: list[str] = []
    for candidate in (primary, category.label):
        if candidate and candidate not in queries:
            queries.append(candidate)

    for idx, query in enumerate(queries):
        top_k = 3 if idx == 0 else min(settings.policy_search_top_k + 3, 8)
        hits = await client.search_contract(
            SearchRequest(
                tenant_id=tenant_id,
                query=query,
                document_id=contract_document_id,
                kind=DocumentKind.CONTRACT,
                top_k=top_k,
            )
        )
        if hits:
            return hits, {
                "contract_retrieval_method": "search",
                "contract_query": query,
            }

    return [], {"contract_retrieval_method": "none"}


async def _policy_exact_hit(
    client: DocumentMCPClient,
    *,
    tenant_id: str,
    document_id: UUID,
    section_id: str,
) -> RetrievalHit | None:
    if not section_id:
        return None
    section = await client.get_section(
        GetSectionRequest(
            tenant_id=tenant_id,
            document_id=document_id,
            section_id=section_id,
        )
    )
    if section is None:
        return None
    return _hit_from_section(section)


async def _policy_search_hits(
    client: DocumentMCPClient,
    *,
    tenant_id: str,
    category: ReviewCategory,
    document_id: UUID | None,
    contract_type: str | None,
    policy_type: str | None,
    settings: ReviewSettings,
    top_k: int | None = None,
) -> list[RetrievalHit]:
    primary = _primary_query(category)
    queries: list[str] = []
    for candidate in (primary, category.label):
        if candidate and candidate not in queries:
            queries.append(candidate)

    limit = top_k or settings.policy_search_top_k

    for idx, query in enumerate(queries):
        request = SearchRequest(
            tenant_id=tenant_id,
            query=query,
            kind=DocumentKind.POLICY,
            policy_type=policy_type,
            contract_type=contract_type,
            top_k=limit if idx == 0 else min(limit + 3, 8),
        )
        if document_id is not None:
            request = request.model_copy(update={"document_id": document_id})
        hits = await client.search_policy(request)
        if hits:
            return hits
    return []


async def _try_fetch_policy(
    client: DocumentMCPClient,
    catalog: PolicyCatalogClient,
    *,
    tenant_id: str,
    policy_ref: str,
    policy_type: str | None,
) -> tuple[UUID | None, dict[str, Any] | None]:
    registry = await client.get_policy_by_ref(tenant_id, policy_ref)
    if registry is not None and registry.index_status == "indexed":
        return registry.document_id, {
            "document_id": str(registry.document_id),
            "title": registry.title,
            "policy_ref": policy_ref,
        }

    document = await catalog.fetch_policy(tenant_id, policy_ref)
    if document is None:
        return None, None
    result, entry = await index_fetched_policy(
        client,
        tenant_id=tenant_id,
        document=document,
        policy_ref=policy_ref,
        default_policy_type=policy_type,
    )
    return result.document_id, entry


async def resolve_policy_hits(
    *,
    client: DocumentMCPClient,
    catalog: PolicyCatalogClient | None,
    tenant_id: str,
    category: ReviewCategory,
    contract_document_id: UUID,
    contract_type: str | None,
    policy_type: str | None,
    fetched_refs: set[str],
    policy_ref_by_doc: dict[str, str],
    settings: ReviewSettings,
    fetch_lock: asyncio.Lock | None = None,
) -> tuple[list[RetrievalHit], list[RetrievalHit], dict[str, Any]]:
    """Resolve policy + contract hits using exact → search → catalog fetch ladder."""
    contract_hits, contract_meta = await _search_contract_hits(
        client,
        tenant_id=tenant_id,
        contract_document_id=contract_document_id,
        category=category,
        settings=settings,
    )
    meta: dict[str, Any] = {
        "retrieval_attempts": 0,
        **contract_meta,
    }

    policy_doc_id = category.policy_document_id
    policy_section_id = category.policy_section_id

    # Attempt 0: exact section lookup
    if policy_doc_id is not None and policy_section_id:
        exact = await _policy_exact_hit(
            client,
            tenant_id=tenant_id,
            document_id=policy_doc_id,
            section_id=policy_section_id,
        )
        meta["retrieval_attempts"] = 1
        if exact is not None:
            meta["retrieval_method"] = "exact"
            return [exact], contract_hits, meta

    # Attempt 1: lexical search
    policy_hits = await _policy_search_hits(
        client,
        tenant_id=tenant_id,
        category=category,
        document_id=policy_doc_id,
        contract_type=contract_type,
        policy_type=policy_type,
        settings=settings,
    )
    meta["retrieval_attempts"] = 2
    if policy_hits:
        meta["retrieval_method"] = "search"
        return policy_hits, contract_hits, meta

    # Attempt 2: catalog fetch + retry (when policy_ref known)
    policy_ref: str | None = None
    if policy_doc_id is not None:
        policy_ref = policy_ref_by_doc.get(str(policy_doc_id))

    if (
        catalog is not None
        and settings.policy_fetch_enabled
        and policy_ref
        and policy_ref not in fetched_refs
    ):
        if fetch_lock is not None:
            async with fetch_lock:
                if policy_ref in fetched_refs:
                    new_doc_id = None
                    for doc_id, ref in policy_ref_by_doc.items():
                        if ref == policy_ref:
                            new_doc_id = UUID(str(doc_id))
                            break
                else:
                    new_doc_id, _entry = await _try_fetch_policy(
                        client,
                        catalog,
                        tenant_id=tenant_id,
                        policy_ref=policy_ref,
                        policy_type=policy_type,
                    )
        else:
            new_doc_id, _entry = await _try_fetch_policy(
                client,
                catalog,
                tenant_id=tenant_id,
                policy_ref=policy_ref,
                policy_type=policy_type,
            )

        meta["retrieval_attempts"] = 3
        if new_doc_id is not None:
            fetched_refs.add(policy_ref)
            policy_ref_by_doc[str(new_doc_id)] = policy_ref
            meta["fetched_policy"] = True
            meta["policy_ref"] = policy_ref

            if policy_section_id:
                exact = await _policy_exact_hit(
                    client,
                    tenant_id=tenant_id,
                    document_id=new_doc_id,
                    section_id=policy_section_id,
                )
                if exact is not None:
                    meta["retrieval_method"] = "exact_after_fetch"
                    return [exact], contract_hits, meta

            policy_hits = await _policy_search_hits(
                client,
                tenant_id=tenant_id,
                category=category,
                document_id=new_doc_id,
                contract_type=contract_type,
                policy_type=policy_type,
                settings=settings,
            )
            if policy_hits:
                meta["retrieval_method"] = "search_after_fetch"
                return policy_hits, contract_hits, meta

    meta["retrieval_method"] = "none"
    return [], contract_hits, meta


async def resolve_all_policy_hits(
    *,
    client: DocumentMCPClient,
    catalog: PolicyCatalogClient | None,
    tenant_id: str,
    categories: list[ReviewCategory],
    contract_document_id: UUID,
    contract_type: str | None,
    policy_type: str | None,
    fetched_refs: set[str],
    policy_ref_by_doc: dict[str, str],
    settings: ReviewSettings,
) -> tuple[
    dict[str, list[RetrievalHit]],
    dict[str, list[RetrievalHit]],
    dict[str, dict[str, Any]],
    list[str],
]:
    """Resolve all categories in parallel; return hit maps and warnings."""
    fetch_lock = asyncio.Lock()
    warnings: list[str] = []

    async def one(category: ReviewCategory) -> tuple[str, list[RetrievalHit], list[RetrievalHit], dict[str, Any]]:
        return (
            category.category_id,
            *await resolve_policy_hits(
                client=client,
                catalog=catalog,
                tenant_id=tenant_id,
                category=category,
                contract_document_id=contract_document_id,
                contract_type=contract_type,
                policy_type=policy_type,
                fetched_refs=fetched_refs,
                policy_ref_by_doc=policy_ref_by_doc,
                settings=settings,
                fetch_lock=fetch_lock,
            ),
        )

    results = await gather_limited(
        [one(category) for category in categories],
        limit=settings.compliance_retrieval_concurrency,
    )

    policy_hits: dict[str, list[RetrievalHit]] = {}
    contract_hits: dict[str, list[RetrievalHit]] = {}
    retrieval_meta: dict[str, dict[str, Any]] = {}

    for result in results:
        if isinstance(result, BaseException):
            warnings.append(f"retrieval failed: {result}")
            logger.warning("parallel retrieval error: %s", result)
            continue
        category_id, p_hits, c_hits, meta = result
        policy_hits[category_id] = p_hits
        contract_hits[category_id] = c_hits
        retrieval_meta[category_id] = meta

    return policy_hits, contract_hits, retrieval_meta, warnings
