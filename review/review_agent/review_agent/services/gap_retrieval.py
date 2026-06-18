"""Retrieve policy text for gaps flagged in hybrid Pass 1."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from document_core.schemas.chunk import DocumentKind, RetrievalHit, SearchRequest

from review_agent.clients.document_client import DocumentMCPClient
from review_agent.config import ReviewSettings, get_settings
from review_agent.schemas.gap_request import GapRequest
from review_agent.schemas.review_category import ReviewCategory
from review_agent.services.async_limits import gather_limited
from review_agent.services.policy_retrieval import resolve_policy_hits

logger = logging.getLogger(__name__)


def collect_gap_requests(raw: list[dict[str, Any]]) -> list[GapRequest]:
    """Parse and dedupe gap requests by topic + category."""
    seen: set[tuple[str, str]] = set()
    result: list[GapRequest] = []
    for item in raw:
        gap = GapRequest.model_validate(item)
        key = (gap.category_id or "", gap.policy_topic.strip().lower())
        if key in seen and key != ("", ""):
            continue
        seen.add(key)
        result.append(gap)
    return result


async def _search_policy_for_gap(
    client: DocumentMCPClient,
    *,
    tenant_id: str,
    gap: GapRequest,
    contract_type: str | None,
    policy_type: str | None,
    policy_document_ids: list[UUID] | None,
    settings: ReviewSettings,
) -> list[RetrievalHit]:
    queries = list(gap.suggested_search_queries)
    if gap.policy_topic and gap.policy_topic not in queries:
        queries.insert(0, gap.policy_topic)
    if not queries:
        return []

    for query in queries:
        request = SearchRequest(
            tenant_id=tenant_id,
            query=query,
            kind=DocumentKind.POLICY,
            policy_type=policy_type,
            contract_type=contract_type,
            top_k=settings.policy_search_top_k,
        )
        if policy_document_ids and len(policy_document_ids) == 1:
            request = request.model_copy(update={"document_id": policy_document_ids[0]})
        hits = await client.search_policy(request)
        if hits:
            return hits
    return []


async def resolve_gap_hits(
    client: DocumentMCPClient,
    *,
    tenant_id: str,
    gaps: list[GapRequest],
    categories_by_id: dict[str, ReviewCategory],
    contract_document_id: UUID,
    contract_type: str | None,
    policy_type: str | None,
    policy_document_ids: list[str] | None,
    fetched_refs: set[str],
    policy_ref_by_doc: dict[str, str],
    catalog,
    settings: ReviewSettings | None = None,
) -> dict[str, list[RetrievalHit]]:
    """Resolve policy hits for each gap request (parallel search)."""
    cfg = settings or get_settings()
    doc_ids: list[UUID] | None = None
    if policy_document_ids:
        parsed: list[UUID] = []
        for value in policy_document_ids:
            try:
                parsed.append(UUID(str(value)))
            except ValueError:
                continue
        doc_ids = parsed or None

    async def one(gap: GapRequest) -> tuple[str, list[RetrievalHit]]:
        hits: list[RetrievalHit] = []
        category = categories_by_id.get(gap.category_id or "")
        if category is not None:
            p_hits, _, _ = await resolve_policy_hits(
                client=client,
                catalog=catalog,
                tenant_id=tenant_id,
                category=category,
                contract_document_id=contract_document_id,
                contract_type=contract_type,
                policy_type=policy_type,
                fetched_refs=fetched_refs,
                policy_ref_by_doc=policy_ref_by_doc,
                settings=cfg,
            )
            hits = p_hits
        if not hits:
            hits = await _search_policy_for_gap(
                client,
                tenant_id=tenant_id,
                gap=gap,
                contract_type=contract_type,
                policy_type=policy_type,
                policy_document_ids=doc_ids,
                settings=cfg,
            )
        return gap.request_id, hits

    pairs = await gather_limited(
        [one(gap) for gap in gaps],
        limit=cfg.compliance_retrieval_concurrency,
    )
    result: dict[str, list[RetrievalHit]] = {}
    for item in pairs:
        if isinstance(item, BaseException):
            logger.warning("gap retrieval failed: %s", item)
            continue
        request_id, hits = item
        result[request_id] = hits
    return result
