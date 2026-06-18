"""Sync policy text from external catalog (Java — any source) into document index."""

from __future__ import annotations

import logging

import httpx

from document_core.schemas.chunk import DocumentKind, IngestRequest, IngestResult, StructureConfidence
from document_core.schemas.registry import SyncPolicyFromCatalogRequest
from document_core.services.ingest import ingest_document
from document_core.services.registry import (
    get_policy_by_ref,
    register_policy,
    stable_policy_document_id,
)
from document_core.schemas.registry import RegisterPolicyRequest
from document_core.store.memory_store import get_store
from document_core.store.protocol import DocumentStore

logger = logging.getLogger(__name__)


class CatalogPolicyPayload:
    """Parsed catalog response (matches Java catalog API contract)."""

    def __init__(
        self,
        *,
        title: str,
        text: str,
        policy_type: str | None,
        applies_to_contract_types: list[str],
        document_id,
        metadata: dict,
    ) -> None:
        self.title = title
        self.text = text
        self.policy_type = policy_type
        self.applies_to_contract_types = applies_to_contract_types
        self.document_id = document_id
        self.metadata = metadata


async def fetch_policy_from_catalog(
    catalog_url: str,
    tenant_id: str,
    policy_ref: str,
    *,
    timeout_seconds: float = 30.0,
) -> CatalogPolicyPayload | None:
    base = catalog_url.rstrip("/")
    url = f"{base}/tenants/{tenant_id}/policies/{policy_ref}"
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(url)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("catalog fetch failed ref=%s: %s", policy_ref, exc)
        return None

    from uuid import UUID

    doc_id = data.get("document_id")
    parsed_id = UUID(str(doc_id)) if doc_id else None
    return CatalogPolicyPayload(
        title=data["title"],
        text=data["text"],
        policy_type=data.get("policy_type"),
        applies_to_contract_types=list(data.get("applies_to_contract_types") or []),
        document_id=parsed_id,
        metadata=dict(data.get("metadata") or {}),
    )


async def sync_policy_from_catalog(
    request: SyncPolicyFromCatalogRequest,
    *,
    catalog_url: str,
    store: DocumentStore | None = None,
) -> IngestResult:
    doc_store = store or get_store()
    existing = get_policy_by_ref(request.tenant_id, request.policy_ref, store=doc_store)
    if (
        existing is not None
        and existing.index_status == "indexed"
        and not request.force_reindex
    ):
        return IngestResult(
            document_id=existing.document_id,
            tenant_id=request.tenant_id,
            kind=DocumentKind.POLICY,
            title=existing.title,
            parent_count=0,
            child_count=0,
            structure_confidence=StructureConfidence.HIGH,
        )

    payload = await fetch_policy_from_catalog(
        catalog_url,
        request.tenant_id,
        request.policy_ref,
    )
    if payload is None:
        doc_store.set_policy_index_status(
            request.tenant_id,
            stable_policy_document_id(request.tenant_id, request.policy_ref),
            "failed",
            error="catalog_not_found",
        )
        raise ValueError(f"policy not found in catalog: {request.policy_ref}")

    document_id = stable_policy_document_id(
        request.tenant_id,
        request.policy_ref,
        payload.document_id,
    )
    source = str(payload.metadata.get("source", "catalog"))
    register_policy(
        RegisterPolicyRequest(
            tenant_id=request.tenant_id,
            policy_ref=request.policy_ref,
            title=payload.title,
            document_id=document_id,
            policy_type=payload.policy_type,
            applies_to_contract_types=payload.applies_to_contract_types,
            source=source,
            metadata=payload.metadata,
        ),
        store=doc_store,
    )

    meta = {
        "policy_ref": request.policy_ref,
        "policy_title": payload.title,
        **payload.metadata,
    }
    try:
        result = await ingest_document(
            IngestRequest(
                tenant_id=request.tenant_id,
                document_id=document_id,
                title=payload.title,
                kind=DocumentKind.POLICY,
                text=payload.text,
                policy_type=payload.policy_type,
                applies_to_contract_types=payload.applies_to_contract_types,
                metadata=meta,
            ),
            store=doc_store,
        )
        return result
    except Exception as exc:
        doc_store.set_policy_index_status(
            request.tenant_id,
            document_id,
            "failed",
            error=str(exc),
        )
        raise
