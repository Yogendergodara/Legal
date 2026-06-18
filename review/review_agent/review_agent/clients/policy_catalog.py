"""Policy catalog client — fetch policy documents from external store (Java/Drive later)."""

from __future__ import annotations

import logging
from typing import Any, Protocol
from uuid import UUID

import httpx
from pydantic import BaseModel, Field

from document_core.schemas.chunk import DocumentKind, IngestRequest, IngestResult
from document_core.services.registry import stable_policy_document_id
from review_agent.clients.document_client import DocumentMCPClient

logger = logging.getLogger(__name__)

_catalog_override: PolicyCatalogClient | None = None


class PolicyDocument(BaseModel):
    """Policy payload returned by the catalog."""

    ref: str
    title: str
    text: str
    policy_type: str | None = None
    applies_to_contract_types: list[str] = Field(default_factory=list)
    document_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PolicyCatalogClient(Protocol):
    async def fetch_policy(self, tenant_id: str, policy_ref: str) -> PolicyDocument | None: ...


class StubPolicyCatalogClient:
    """In-memory catalog for tests and local development."""

    def __init__(self, policies: dict[str, PolicyDocument] | None = None) -> None:
        self._policies = dict(policies or {})

    def register(self, document: PolicyDocument) -> None:
        self._policies[document.ref] = document

    async def fetch_policy(self, tenant_id: str, policy_ref: str) -> PolicyDocument | None:
        _ = tenant_id
        return self._policies.get(policy_ref)


class HttpPolicyCatalogClient:
    """HTTP catalog client — GET {base}/tenants/{tenant}/policies/{ref}."""

    def __init__(self, base_url: str, *, timeout_seconds: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds

    async def fetch_policy(self, tenant_id: str, policy_ref: str) -> PolicyDocument | None:
        url = f"{self._base_url}/tenants/{tenant_id}/policies/{policy_ref}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as http:
                response = await http.get(url)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            data = response.json()
            return PolicyDocument.model_validate({**data, "ref": policy_ref})
        except Exception as exc:  # noqa: BLE001
            logger.warning("policy catalog fetch failed ref=%s: %s", policy_ref, exc)
            return None


def set_policy_catalog(catalog: PolicyCatalogClient | None) -> None:
    """Override catalog instance (tests)."""
    global _catalog_override
    _catalog_override = catalog


def get_policy_catalog(*, catalog_url: str | None, fetch_enabled: bool) -> PolicyCatalogClient | None:
    if not fetch_enabled:
        return None
    if _catalog_override is not None:
        return _catalog_override
    if catalog_url:
        return HttpPolicyCatalogClient(catalog_url)
    return None


async def index_fetched_policy(
    client: DocumentMCPClient,
    *,
    tenant_id: str,
    document: PolicyDocument,
    policy_ref: str,
    default_policy_type: str | None = None,
) -> tuple[IngestResult, dict[str, Any]]:
    """Index a catalog policy with stable document_id (idempotent re-fetch)."""
    document_id = stable_policy_document_id(tenant_id, policy_ref, document.document_id)
    result = await client.index_policy(
        IngestRequest(
            tenant_id=tenant_id,
            document_id=document_id,
            title=document.title,
            kind=DocumentKind.POLICY,
            text=document.text,
            policy_type=document.policy_type or default_policy_type,
            applies_to_contract_types=document.applies_to_contract_types,
            metadata={"policy_ref": policy_ref, **document.metadata},
        )
    )
    entry = {
        "document_id": str(result.document_id),
        "title": document.title,
        "policy_type": document.policy_type or default_policy_type,
        "applies_to_contract_types": list(document.applies_to_contract_types),
        "policy_ref": policy_ref,
    }
    return result, entry
