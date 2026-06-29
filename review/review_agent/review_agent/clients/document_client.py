"""HTTP client for the Document MCP server."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from uuid import UUID

import httpx

from review_agent.errors import FatalPipelineError, MCPUnreachableError, RecoverableError
from review_agent.observability.metrics import record_mcp_request
from review_agent.resilience.circuit_breaker import get_mcp_breaker

logger = logging.getLogger(__name__)

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
from document_core.schemas.policy_catalog import CatalogSearchHit, CatalogSearchRequest

_PATH_TIMEOUTS: dict[str, float] = {
    "/health": 5.0,
    "/tools/ingest_document": 120.0,
    "/tools/index_policy": 120.0,
    "/tools/sync_policies": 900.0,
    "/tools/search_policy": 30.0,
    "/tools/search_policy_by_categories": 30.0,
    "/tools/search_policy_catalog": 30.0,
    "/tools/search_policy_fts": 30.0,
    "/tools/search_policy_recall": 30.0,
    "/tools/search_contract": 30.0,
}

STALE_MCP_PROBE_MESSAGE = (
    "document-mcp does not support SearchRequest.metadata — likely a stale process on "
    "port 8003. Run: Legal ai/scripts/stop_document_mcp.ps1 then "
    "start_document_mcp.ps1 -Replace"
)


class DocumentMCPClient:
    """Typed client for document-mcp /tools/* endpoints.

    Uses one persistent ``httpx.AsyncClient`` per instance when ``http_client`` is not
    injected. Callers should ``await client.aclose()`` when done (or use ``open()``).
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 60.0,
        max_retries: int = 3,
        http_client: httpx.AsyncClient | None = None,
        health_timeout_seconds: float | None = None,
        ingest_timeout_seconds: float | None = None,
        search_timeout_seconds: float | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self._health_timeout = health_timeout_seconds if health_timeout_seconds is not None else 5.0
        self._ingest_timeout = ingest_timeout_seconds if ingest_timeout_seconds is not None else 120.0
        self._search_timeout = search_timeout_seconds if search_timeout_seconds is not None else 30.0
        self._owns_client = http_client is None
        if http_client is None:
            from review_agent.config import get_settings

            cfg = get_settings()
            limits = httpx.Limits(
                max_keepalive_connections=cfg.mcp_http_max_keepalive_connections,
                max_connections=cfg.mcp_http_max_connections,
            )
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(timeout_seconds),
                limits=limits,
            )
        else:
            self._client = http_client
        self._injected_client = None if self._owns_client else self._client

    @classmethod
    @asynccontextmanager
    async def open(cls, base_url: str, **kwargs: Any) -> AsyncIterator[DocumentMCPClient]:
        client = cls(base_url, **kwargs)
        try:
            yield client
        finally:
            await client.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _timeout_for(self, path: str) -> float:
        if path == "/health":
            return self._health_timeout
        if path in ("/tools/ingest_document", "/tools/index_policy"):
            return self._ingest_timeout
        if path in _PATH_TIMEOUTS and path != "/health":
            mapped = _PATH_TIMEOUTS[path]
            if mapped == 30.0:
                return self._search_timeout
            if mapped == 120.0:
                return self._ingest_timeout
            return mapped
        return self.timeout_seconds

    def _request_url(self, path: str) -> str:
        if self._owns_client:
            return path
        return f"{self.base_url}{path}"

    async def _wait_healthy(self, max_wait: float = 15.0) -> None:
        deadline = time.monotonic() + max_wait
        health_timeout = self._timeout_for("/health")
        while time.monotonic() < deadline:
            try:
                response = await self._client.get(
                    self._request_url("/health"),
                    timeout=health_timeout,
                )
                if response.status_code == 200:
                    data = response.json()
                    if data.get("status") == "ok":
                        return
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(1.0)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        timeout: float | None = None,
        allow_404: bool = False,
        raise_for_status: bool = True,
    ) -> httpx.Response:
        breaker = get_mcp_breaker()
        if not breaker.allow():
            record_mcp_request(path, "circuit_open")
            raise MCPUnreachableError(
                f"circuit_open:mcp — document-mcp breaker is open, skipping {method} {path}"
            )

        url = self._request_url(path)
        resolved_timeout = timeout if timeout is not None else self._timeout_for(path)
        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                response = await self._client.request(
                    method,
                    url,
                    json=json,
                    timeout=resolved_timeout,
                )
                if allow_404 and response.status_code == 404:
                    breaker.record_success()
                    return response
                if raise_for_status:
                    response.raise_for_status()
                breaker.record_success()
                record_mcp_request(path, str(response.status_code))
                return response
            except httpx.HTTPStatusError as exc:
                if allow_404 and exc.response.status_code == 404:
                    breaker.record_success()
                    return exc.response
                status = exc.response.status_code
                if 400 <= status < 500:
                    record_mcp_request(path, str(status))
                    raise FatalPipelineError(
                        f"document-mcp {method} {path} returned {status}"
                    ) from exc
                # 5xx — record failure, may retry
                breaker.record_failure()
                last_error = exc
            except (httpx.ConnectError, httpx.ReadTimeout) as exc:
                breaker.record_failure()
                last_error = exc
                if attempt < self.max_retries:
                    await self._wait_healthy()
                    continue
            except Exception as exc:  # noqa: BLE001
                breaker.record_failure()
                last_error = exc

            if attempt < self.max_retries:
                await asyncio.sleep(min(0.5 * attempt, 2.0))

        if isinstance(last_error, (httpx.ConnectError, httpx.ReadTimeout)):
            record_mcp_request(path, "error")
            raise MCPUnreachableError(
                f"document-mcp {method} {path} unreachable after {self.max_retries} attempts"
            ) from last_error
        record_mcp_request(path, "error")
        raise RecoverableError(
            f"document-mcp {method} {path} failed: {last_error}"
        ) from last_error

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return (await self._request("POST", path, json=payload)).json()

    async def _post_nullable(self, path: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        response = await self._request("POST", path, json=payload, allow_404=True)
        if response.status_code == 404:
            return None
        return response.json()

    async def health(self) -> dict[str, Any]:
        return (await self._request("GET", "/health")).json()

    async def probe_search_metadata_capability(self, *, tenant_id: str = "e2e-demo") -> None:
        """Raise ``RuntimeError`` when document-mcp lacks metadata category search."""
        health = await self.health()
        capabilities = list(health.get("capabilities") or [])
        if "search_request_metadata" not in capabilities:
            raise RuntimeError(STALE_MCP_PROBE_MESSAGE)

        request = SearchRequest(tenant_id=tenant_id, query="preflight-probe", top_k=1)
        payload = request.model_dump(mode="json")
        payload["metadata"] = {**(payload.get("metadata") or {}), "categories": []}
        try:
            response = await self._request(
                "POST",
                "/tools/search_policy_by_categories",
                json=payload,
                raise_for_status=False,
            )
        except RuntimeError as exc:
            raise RuntimeError(f"document-mcp category search probe failed: {exc}") from exc

        if response.status_code >= 400:
            body = (response.text or "").lower()
            if "metadata" in body:
                raise RuntimeError(STALE_MCP_PROBE_MESSAGE)
            raise RuntimeError(
                "document-mcp category search probe failed "
                f"({response.status_code}): {response.text[:300]}"
            )

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

    async def list_policy_ids_by_categories(
        self,
        tenant_id: str,
        categories: list[str],
        *,
        contract_type: str | None = None,
    ) -> list[UUID]:
        data = await self._post(
            "/tools/list_policy_ids_by_categories",
            {
                "tenant_id": tenant_id,
                "categories": categories,
                "contract_type": contract_type,
            },
        )
        return [UUID(doc_id) for doc_id in data.get("document_ids", [])]

    async def search_policy_catalog(self, request: CatalogSearchRequest) -> list[CatalogSearchHit]:
        data = await self._post("/tools/search_policy_catalog", request.model_dump(mode="json"))
        return [CatalogSearchHit.model_validate(hit) for hit in data.get("results", [])]

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
        data = await self._post_nullable(
            "/tools/get_section",
            request.model_dump(mode="json"),
        )
        return IndexedChunk.model_validate(data) if data else None

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

    async def register_contract(self, request) -> Any:
        from document_core.schemas.registry import PolicyRegistryRecord, RegisterContractRequest

        payload = (
            request
            if isinstance(request, RegisterContractRequest)
            else RegisterContractRequest.model_validate(request)
        )
        data = await self._post("/tools/register_contract", payload.model_dump(mode="json"))
        return PolicyRegistryRecord.model_validate(data)

    async def get_policy_by_ref(self, tenant_id: str, policy_ref: str):
        from document_core.schemas.registry import GetPolicyByRefRequest, PolicyRegistryRecord

        data = await self._post_nullable(
            "/tools/get_policy_by_ref",
            GetPolicyByRefRequest(tenant_id=tenant_id, policy_ref=policy_ref).model_dump(mode="json"),
        )
        return PolicyRegistryRecord.model_validate(data) if data else None

    async def get_contract_by_ref(self, tenant_id: str, contract_ref: str):
        from document_core.schemas.registry import GetPolicyByRefRequest, PolicyRegistryRecord

        data = await self._post_nullable(
            "/tools/get_contract_by_ref",
            GetPolicyByRefRequest(tenant_id=tenant_id, policy_ref=contract_ref).model_dump(mode="json"),
        )
        return PolicyRegistryRecord.model_validate(data) if data else None

    async def delete_policy(self, tenant_id: str, policy_ref: str):
        from document_core.schemas.registry import DeletePolicyRequest, DeletePolicyResult

        data = await self._post(
            "/tools/delete_policy",
            DeletePolicyRequest(tenant_id=tenant_id, policy_ref=policy_ref).model_dump(mode="json"),
        )
        return DeletePolicyResult.model_validate(data)

    async def list_policy_registry(
        self,
        tenant_id: str,
        *,
        kind: str | None = None,
        index_status: str | None = None,
    ):
        from document_core.schemas.registry import ListPolicyRegistryResponse

        payload: dict[str, Any] = {"tenant_id": tenant_id}
        if kind is not None:
            payload["kind"] = kind
        if index_status is not None:
            payload["index_status"] = index_status
        data = await self._post("/tools/list_policy_registry", payload)
        return ListPolicyRegistryResponse.model_validate(data)

    async def sync_policies(self, request) -> Any:
        from document_core.schemas.policy_sync import SyncPoliciesRequest, SyncPoliciesResponse

        payload = (
            request
            if isinstance(request, SyncPoliciesRequest)
            else SyncPoliciesRequest.model_validate(request)
        )
        data = await self._post("/tools/sync_policies", payload.model_dump(mode="json"))
        return SyncPoliciesResponse.model_validate(data)
