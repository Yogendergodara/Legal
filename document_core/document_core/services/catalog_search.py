"""Search tenant policy catalog profiles (Phase R0)."""

from __future__ import annotations

import asyncio

from document_core.schemas.policy_catalog import CatalogSearchHit, CatalogSearchRequest
from document_core.store.memory_store import get_store
from document_core.store.protocol import DocumentStore


async def search_policy_catalog(
    request: CatalogSearchRequest,
    *,
    store: DocumentStore | None = None,
) -> list[CatalogSearchHit]:
    doc_store = store or get_store()
    if hasattr(doc_store, "search_policy_catalog_async"):
        return await doc_store.search_policy_catalog_async(request)
    if hasattr(doc_store, "search_policy_catalog"):
        return await asyncio.to_thread(doc_store.search_policy_catalog, request)
    return []
