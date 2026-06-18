"""HTTP client for the Document MCP server."""

from __future__ import annotations

import asyncio
import time
from typing import Any
from uuid import UUID

import httpx

from document_core.schemas.chunk import (
    DocumentKind,
    GetSectionRequest,
    GroundingCheckRequest,
    GroundingCheckResult,
    IndexedChunk,
    IngestRequest,
    IngestResult,
    ListSectionsRequest,
    RetrievalHit,
    SearchRequest,
)


class DocumentMCPClient:
    """Typed client for document-mcp /tools/* endpoints."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 60.0,
        max_retries: int = 3,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self._injected_client = http_client

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                if self._injected_client is not None:
                    response = await self._injected_client.post(url, json=payload)
                else:
                    async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                        response = await client.post(url, json=payload)
                response.raise_for_status()
                return response.json()
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt < self.max_retries:
                    await asyncio.sleep(min(0.5 * attempt, 2.0))
        raise RuntimeError(f"document-mcp POST {path} failed: {last_error}") from last_error

    async def health(self) -> dict[str, Any]:
        if self._injected_client is not None:
            response = await self._injected_client.get(f"{self.base_url}/health")
        else:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(f"{self.base_url}/health")
        response.raise_for_status()
        return response.json()

    async def ingest_document(self, request: IngestRequest) -> IngestResult:
        data = await self._post("/tools/ingest_document", request.model_dump(mode="json"))
        return IngestResult.model_validate(data)

    async def index_policy(self, request: IngestRequest) -> IngestResult:
        data = await self._post("/tools/index_policy", request.model_dump(mode="json"))
        return IngestResult.model_validate(data)

    async def search_contract(self, request: SearchRequest) -> list[RetrievalHit]:
        data = await self._post("/tools/search_contract", request.model_dump(mode="json"))
        return [RetrievalHit.model_validate(hit) for hit in data.get("results", [])]

    async def search_policy(self, request: SearchRequest) -> list[RetrievalHit]:
        data = await self._post("/tools/search_policy", request.model_dump(mode="json"))
        return [RetrievalHit.model_validate(hit) for hit in data.get("results", [])]

    async def search_policy_fts(self, request: SearchRequest) -> list[RetrievalHit]:
        data = await self._post("/tools/search_policy_fts", request.model_dump(mode="json"))
        return [RetrievalHit.model_validate(hit) for hit in data.get("results", [])]

    async def search_policy_recall(self, request: SearchRequest) -> list[RetrievalHit]:
        data = await self._post("/tools/search_policy_recall", request.model_dump(mode="json"))
        return [RetrievalHit.model_validate(hit) for hit in data.get("results", [])]

    async def search_policy_by_categories(
        self,
        request: SearchRequest,
        *,
        categories: list[str],
    ) -> list[RetrievalHit]:
        payload = request.model_dump(mode="json")
        payload["metadata"] = {**(payload.get("metadata") or {}), "categories": categories}
        data = await self._post("/tools/search_policy_by_categories", payload)
        return [RetrievalHit.model_validate(hit) for hit in data.get("results", [])]

    async def list_sections(self, request: ListSectionsRequest) -> list[IndexedChunk]:
        data = await self._post("/tools/list_sections", request.model_dump(mode="json"))
        return [IndexedChunk.model_validate(item) for item in data.get("sections", [])]

    async def list_policies(self, tenant_id: str) -> list[UUID]:
        data = await self._post(
            "/tools/list_policies",
            {"tenant_id": tenant_id, "kind": DocumentKind.POLICY.value},
        )
        return [UUID(doc_id) for doc_id in data.get("document_ids", [])]

    async def get_section(self, request: GetSectionRequest) -> IndexedChunk | None:
        try:
            if self._injected_client is not None:
                response = await self._injected_client.post(
                    f"{self.base_url}/tools/get_section",
                    json=request.model_dump(mode="json"),
                )
            else:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    response = await client.post(
                        f"{self.base_url}/tools/get_section",
                        json=request.model_dump(mode="json"),
                    )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return IndexedChunk.model_validate(response.json())
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

    async def verify_quote(self, request: GroundingCheckRequest) -> GroundingCheckResult:
        data = await self._post("/tools/verify_quote", request.model_dump(mode="json"))
        return GroundingCheckResult.model_validate(data)

    async def verify_policy_quote(self, request: GroundingCheckRequest) -> GroundingCheckResult:
        data = await self._post("/tools/verify_policy_quote", request.model_dump(mode="json"))
        return GroundingCheckResult.model_validate(data)

    async def register_policy(self, request) -> Any:
        from document_core.schemas.registry import PolicyRegistryRecord, RegisterPolicyRequest

        payload = request if isinstance(request, RegisterPolicyRequest) else RegisterPolicyRequest.model_validate(request)
        data = await self._post("/tools/register_policy", payload.model_dump(mode="json"))
        return PolicyRegistryRecord.model_validate(data)

    async def get_policy_by_ref(self, tenant_id: str, policy_ref: str):
        from document_core.schemas.registry import GetPolicyByRefRequest, PolicyRegistryRecord

        try:
            if self._injected_client is not None:
                response = await self._injected_client.post(
                    f"{self.base_url}/tools/get_policy_by_ref",
                    json=GetPolicyByRefRequest(
                        tenant_id=tenant_id,
                        policy_ref=policy_ref,
                    ).model_dump(mode="json"),
                )
            else:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    response = await client.post(
                        f"{self.base_url}/tools/get_policy_by_ref",
                        json=GetPolicyByRefRequest(
                            tenant_id=tenant_id,
                            policy_ref=policy_ref,
                        ).model_dump(mode="json"),
                    )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return PolicyRegistryRecord.model_validate(response.json())
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

    async def sync_policy_from_catalog(self, tenant_id: str, policy_ref: str, *, force_reindex: bool = False):
        from document_core.schemas.registry import SyncPolicyFromCatalogRequest

        data = await self._post(
            "/tools/sync_policy_from_catalog",
            SyncPolicyFromCatalogRequest(
                tenant_id=tenant_id,
                policy_ref=policy_ref,
                force_reindex=force_reindex,
            ).model_dump(mode="json"),
        )
        return IngestResult.model_validate(data)
