"""HTTP client for document-mcp (platform integration)."""

from __future__ import annotations

from uuid import UUID

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
from legal_ai_platform.mcp.base_client import BaseMCPClient


class DocumentMCPClient(BaseMCPClient):
    """Typed client for document-mcp /tools/* endpoints."""

    server_name = "document-mcp"

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
        url = f"{self.base_url}/tools/get_section"
        async with self._acquire_client() as client:
            response = await client.post(url, json=request.model_dump(mode="json"))
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return IndexedChunk.model_validate(response.json())

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

        data = await self._post(
            "/tools/get_policy_by_ref",
            GetPolicyByRefRequest(tenant_id=tenant_id, policy_ref=policy_ref).model_dump(mode="json"),
        )
        return PolicyRegistryRecord.model_validate(data)

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
