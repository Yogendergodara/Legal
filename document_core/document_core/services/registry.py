"""Policy registry: metadata rows in policy_documents (source-agnostic)."""

from __future__ import annotations

from uuid import NAMESPACE_DNS, UUID, uuid5

from document_core.schemas.registry import (
    ListPolicyRegistryRequest,
    ListPolicyRegistryResponse,
    PolicyRegistryRecord,
    RegisterPolicyRequest,
)
from document_core.store.memory_store import get_store
from document_core.store.protocol import DocumentStore


def stable_policy_document_id(
    tenant_id: str,
    policy_ref: str,
    provided: UUID | None = None,
) -> UUID:
    if provided is not None:
        return provided
    return uuid5(NAMESPACE_DNS, f"{tenant_id}:{policy_ref}")


def register_policy(
    request: RegisterPolicyRequest,
    *,
    store: DocumentStore | None = None,
    kind: str = "policy",
) -> PolicyRegistryRecord:
    doc_store = store or get_store()
    document_id = stable_policy_document_id(
        request.tenant_id,
        request.policy_ref,
        request.document_id,
    )
    return doc_store.upsert_policy_registry(
        tenant_id=request.tenant_id,
        document_id=document_id,
        policy_ref=request.policy_ref,
        title=request.title,
        kind=kind,
        policy_type=request.policy_type,
        applies_to_contract_types=request.applies_to_contract_types,
        source=request.source,
        metadata=request.metadata,
        index_status="pending",
    )


def get_policy_by_ref(
    tenant_id: str,
    policy_ref: str,
    *,
    store: DocumentStore | None = None,
) -> PolicyRegistryRecord | None:
    doc_store = store or get_store()
    return doc_store.get_policy_by_ref(tenant_id, policy_ref)


def list_policy_registry(
    request: ListPolicyRegistryRequest,
    *,
    store: DocumentStore | None = None,
) -> ListPolicyRegistryResponse:
    doc_store = store or get_store()
    policies = doc_store.list_policy_registry(
        request.tenant_id,
        kind=request.kind,
        index_status=request.index_status,
    )
    return ListPolicyRegistryResponse(tenant_id=request.tenant_id, policies=policies)
