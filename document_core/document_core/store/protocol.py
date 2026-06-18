"""Document store protocol (pgvector implementation only)."""

from __future__ import annotations

from typing import Literal, Protocol
from uuid import UUID

from document_core.schemas.chunk import DocumentKind, DocumentTree, IndexedChunk, SearchRequest
from document_core.schemas.registry import PolicyRegistryRecord


class DocumentStore(Protocol):
    def save_document(
        self,
        *,
        tree: DocumentTree,
        parents: list[IndexedChunk],
        children: list[IndexedChunk],
    ) -> None: ...

    def get_parents(self, tenant_id: str, document_id: UUID) -> list[IndexedChunk]: ...

    def get_children(self, tenant_id: str, document_id: UUID) -> list[IndexedChunk]: ...

    def get_canonical_text(self, tenant_id: str, document_id: UUID) -> str | None: ...

    def list_documents(
        self,
        tenant_id: str,
        kind: DocumentKind | None = None,
    ) -> list[UUID]: ...

    def get_parent_by_section(
        self,
        tenant_id: str,
        document_id: UUID,
        section_id: str,
    ) -> IndexedChunk | None: ...

    def upsert_policy_registry(
        self,
        *,
        tenant_id: str,
        document_id: UUID,
        policy_ref: str,
        title: str,
        kind: str,
        policy_type: str | None,
        applies_to_contract_types: list[str],
        source: str,
        metadata: dict,
        index_status: Literal["pending", "indexed", "failed"],
    ) -> PolicyRegistryRecord: ...

    def get_policy_by_ref(self, tenant_id: str, policy_ref: str) -> PolicyRegistryRecord | None: ...

    def list_policy_registry(
        self,
        tenant_id: str,
        *,
        kind: str | None = None,
        index_status: str | None = None,
    ) -> list[PolicyRegistryRecord]: ...

    def set_policy_index_status(
        self,
        tenant_id: str,
        document_id: UUID,
        status: Literal["pending", "indexed", "failed"],
        *,
        error: str | None = None,
    ) -> None: ...

    def search_children_scored(
        self,
        request: SearchRequest,
        document_ids: list[UUID],
        *,
        use_hybrid: bool,
    ) -> list[tuple[float, IndexedChunk]]: ...

    def ping(self) -> bool: ...
