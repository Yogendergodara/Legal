"""Async adapter for PgVectorDocumentStore.

Wraps every synchronous store method with ``asyncio.to_thread()`` so that
blocking SQLAlchemy calls run in the default thread-pool executor instead of
stalling the FastAPI/uvicorn event loop.

Usage — replace the bare store with the adapter at startup::

    pg_store = PgVectorDocumentStore(database_url)
    async_store = AsyncDocumentStoreAdapter(pg_store)
    set_store(async_store)          # ← services see async_store
"""

from __future__ import annotations

import asyncio
import functools
from typing import Any, Literal
from uuid import UUID

from document_core.schemas.chunk import (
    DocumentKind,
    DocumentTree,
    IndexedChunk,
    SearchRequest,
)
from document_core.schemas.policy_catalog import CatalogSearchHit, CatalogSearchRequest
from document_core.schemas.registry import PolicyRegistryRecord
from document_core.store.pgvector_store import PgVectorDocumentStore


class AsyncDocumentStoreAdapter:
    """Async wrapper around the synchronous ``PgVectorDocumentStore``.

    Every public method delegates to the underlying sync store inside
    ``asyncio.to_thread()``, which schedules the blocking I/O on the
    default ``ThreadPoolExecutor`` and ``await``\s it without blocking
    the event loop.

    The adapter exposes the same method signatures as the ``DocumentStore``
    protocol so callers that do ``store.method(...)`` keep working.  For
    callers that already ``await`` the result (search services, ingest, …)
    the switch is transparent — ``asyncio.to_thread`` returns an awaitable,
    but calling the *sync* method directly also still works because Python
    simply invokes the underlying sync function.

    .. note::

       We intentionally keep the *sync* method names (not ``async def``)
       at the protocol level so the existing ``DocumentStore`` protocol
       remains satisfied.  The ``search.py`` / ``ingest.py`` service layer
       already wraps calls in ``async def`` — they just happen to call
       sync methods today, and those sync methods are what we offload here.
    """

    def __init__(self, sync_store: PgVectorDocumentStore) -> None:
        self._sync = sync_store

    # ── Expose the raw engine for callers that need it (migrations, etc.) ─
    @property
    def engine(self):
        return self._sync.engine

    # ── Synchronous methods (satisfy DocumentStore protocol directly) ─────
    # These remain synchronous so the Protocol type-check passes and so
    # callers in non-async contexts (tests, CLI scripts) still work.

    def ping(self) -> bool:
        return self._sync.ping()

    def save_document(
        self,
        *,
        tree: DocumentTree,
        parents: list[IndexedChunk],
        children: list[IndexedChunk],
    ) -> None:
        return self._sync.save_document(tree=tree, parents=parents, children=children)

    def get_parents(self, tenant_id: str, document_id: UUID) -> list[IndexedChunk]:
        return self._sync.get_parents(tenant_id, document_id)

    def get_children(self, tenant_id: str, document_id: UUID) -> list[IndexedChunk]:
        return self._sync.get_children(tenant_id, document_id)

    def get_canonical_text(self, tenant_id: str, document_id: UUID) -> str | None:
        return self._sync.get_canonical_text(tenant_id, document_id)

    def list_documents(
        self,
        tenant_id: str,
        kind: DocumentKind | None = None,
    ) -> list[UUID]:
        return self._sync.list_documents(tenant_id, kind)

    def get_parent_by_section(
        self,
        tenant_id: str,
        document_id: UUID,
        section_id: str,
    ) -> IndexedChunk | None:
        return self._sync.get_parent_by_section(tenant_id, document_id, section_id)

    def upsert_policy_registry(
        self,
        *,
        tenant_id: str,
        document_id: UUID,
        policy_ref: str,
        title: str,
        kind: str,
        policy_type: str | None,
        source: str,
        metadata: dict,
        index_status: Literal["pending", "indexed", "failed"],
    ) -> PolicyRegistryRecord:
        return self._sync.upsert_policy_registry(
            tenant_id=tenant_id,
            document_id=document_id,
            policy_ref=policy_ref,
            title=title,
            kind=kind,
            policy_type=policy_type,
            source=source,
            metadata=metadata,
            index_status=index_status,
        )

    def get_policy_by_ref(self, tenant_id: str, policy_ref: str) -> PolicyRegistryRecord | None:
        return self._sync.get_policy_by_ref(tenant_id, policy_ref)

    def get_policy_registry_by_document_id(
        self, tenant_id: str, document_id: UUID,
    ) -> PolicyRegistryRecord | None:
        return self._sync.get_policy_registry_by_document_id(tenant_id, document_id)

    def tombstone_policy_by_ref(self, tenant_id: str, policy_ref: str) -> PolicyRegistryRecord | None:
        return self._sync.tombstone_policy_by_ref(tenant_id, policy_ref)

    def list_policy_registry(
        self,
        tenant_id: str,
        *,
        kind: str | None = None,
        index_status: str | None = None,
    ) -> list[PolicyRegistryRecord]:
        return self._sync.list_policy_registry(tenant_id, kind=kind, index_status=index_status)

    def set_policy_index_status(
        self,
        tenant_id: str,
        document_id: UUID,
        status: Literal["pending", "indexed", "failed"],
        *,
        error: str | None = None,
    ) -> None:
        return self._sync.set_policy_index_status(tenant_id, document_id, status, error=error)

    def search_children_scored(
        self,
        request: SearchRequest,
        document_ids: list[UUID],
        *,
        use_hybrid: bool,
    ) -> list[tuple[float, IndexedChunk]]:
        return self._sync.search_children_scored(request, document_ids, use_hybrid=use_hybrid)

    def search_children_fts(
        self,
        request: SearchRequest,
        document_ids: list[UUID],
    ) -> list[tuple[float, IndexedChunk]]:
        return self._sync.search_children_fts(request, document_ids)

    def list_document_ids_by_categories(
        self,
        tenant_id: str,
        categories: list[str],
        *,
        contract_type: str | None = None,
        kind: DocumentKind = DocumentKind.POLICY,
    ) -> list[UUID]:
        return self._sync.list_document_ids_by_categories(
            tenant_id, categories, contract_type=contract_type, kind=kind,
        )

    # ── Async versions — offload blocking calls to thread pool ────────────
    # Service-layer code (search.py, ingest.py, etc.) should call these
    # when running inside the async event loop.

    async def ping_async(self) -> bool:
        return await asyncio.to_thread(self._sync.ping)

    async def save_document_async(
        self,
        *,
        tree: DocumentTree,
        parents: list[IndexedChunk],
        children: list[IndexedChunk],
    ) -> None:
        return await asyncio.to_thread(
            self._sync.save_document, tree=tree, parents=parents, children=children,
        )

    async def get_parents_async(self, tenant_id: str, document_id: UUID) -> list[IndexedChunk]:
        return await asyncio.to_thread(self._sync.get_parents, tenant_id, document_id)

    async def get_children_async(self, tenant_id: str, document_id: UUID) -> list[IndexedChunk]:
        return await asyncio.to_thread(self._sync.get_children, tenant_id, document_id)

    async def get_canonical_text_async(self, tenant_id: str, document_id: UUID) -> str | None:
        return await asyncio.to_thread(self._sync.get_canonical_text, tenant_id, document_id)

    async def list_documents_async(
        self, tenant_id: str, kind: DocumentKind | None = None,
    ) -> list[UUID]:
        return await asyncio.to_thread(self._sync.list_documents, tenant_id, kind)

    async def get_parent_by_section_async(
        self, tenant_id: str, document_id: UUID, section_id: str,
    ) -> IndexedChunk | None:
        return await asyncio.to_thread(
            self._sync.get_parent_by_section, tenant_id, document_id, section_id,
        )

    async def upsert_policy_registry_async(
        self,
        *,
        tenant_id: str,
        document_id: UUID,
        policy_ref: str,
        title: str,
        kind: str,
        policy_type: str | None,
        source: str,
        metadata: dict,
        index_status: Literal["pending", "indexed", "failed"],
    ) -> PolicyRegistryRecord:
        return await asyncio.to_thread(
            functools.partial(
                self._sync.upsert_policy_registry,
                tenant_id=tenant_id,
                document_id=document_id,
                policy_ref=policy_ref,
                title=title,
                kind=kind,
                policy_type=policy_type,
                source=source,
                metadata=metadata,
                index_status=index_status,
            ),
        )

    async def get_policy_by_ref_async(
        self, tenant_id: str, policy_ref: str,
    ) -> PolicyRegistryRecord | None:
        return await asyncio.to_thread(self._sync.get_policy_by_ref, tenant_id, policy_ref)

    async def get_policy_registry_by_document_id_async(
        self, tenant_id: str, document_id: UUID,
    ) -> PolicyRegistryRecord | None:
        return await asyncio.to_thread(
            self._sync.get_policy_registry_by_document_id, tenant_id, document_id,
        )

    async def tombstone_policy_by_ref_async(
        self, tenant_id: str, policy_ref: str,
    ) -> PolicyRegistryRecord | None:
        return await asyncio.to_thread(self._sync.tombstone_policy_by_ref, tenant_id, policy_ref)

    async def list_policy_registry_async(
        self,
        tenant_id: str,
        *,
        kind: str | None = None,
        index_status: str | None = None,
    ) -> list[PolicyRegistryRecord]:
        return await asyncio.to_thread(
            functools.partial(
                self._sync.list_policy_registry, tenant_id, kind=kind, index_status=index_status,
            ),
        )

    async def set_policy_index_status_async(
        self,
        tenant_id: str,
        document_id: UUID,
        status: Literal["pending", "indexed", "failed"],
        *,
        error: str | None = None,
    ) -> None:
        return await asyncio.to_thread(
            functools.partial(
                self._sync.set_policy_index_status, tenant_id, document_id, status, error=error,
            ),
        )

    async def search_children_scored_async(
        self,
        request: SearchRequest,
        document_ids: list[UUID],
        *,
        use_hybrid: bool,
    ) -> list[tuple[float, IndexedChunk]]:
        return await asyncio.to_thread(
            functools.partial(
                self._sync.search_children_scored, request, document_ids, use_hybrid=use_hybrid,
            ),
        )

    async def search_children_fts_async(
        self,
        request: SearchRequest,
        document_ids: list[UUID],
    ) -> list[tuple[float, IndexedChunk]]:
        return await asyncio.to_thread(self._sync.search_children_fts, request, document_ids)

    async def list_document_ids_by_categories_async(
        self,
        tenant_id: str,
        categories: list[str],
        *,
        contract_type: str | None = None,
        kind: DocumentKind = DocumentKind.POLICY,
    ) -> list[UUID]:
        return await asyncio.to_thread(
            functools.partial(
                self._sync.list_document_ids_by_categories,
                tenant_id,
                categories,
                contract_type=contract_type,
                kind=kind,
            ),
        )

    async def search_policy_catalog_async(
        self,
        request: CatalogSearchRequest,
    ) -> list[CatalogSearchHit]:
        return await asyncio.to_thread(self._sync.search_policy_catalog, request)
