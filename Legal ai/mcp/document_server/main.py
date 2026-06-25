"""FastAPI application for the Document MCP server.

Exposes /tools/* HTTP endpoints (same pattern as retrieval-mcp) so platform
clients can call ingest, search, and grounding without importing server code.
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

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
from document_core.schemas.registry import (
    DeletePolicyRequest,
    DeletePolicyResult,
    GetPolicyByRefRequest,
    ListPolicyRegistryRequest,
    ListPolicyRegistryResponse,
    PolicyRegistryRecord,
    RegisterContractRequest,
    RegisterPolicyRequest,
)
from document_core.services.grounding import verify_quote
from document_core.services.ingest import ingest_document
from document_core.services.registry_async import (
    delete_policy_async,
    get_contract_by_ref_async,
    get_policy_by_ref_async,
    list_policy_registry_async,
    register_contract_async,
    register_policy_async,
)
from document_core.services.search import (
    get_section,
    list_policy_ids_by_categories,
    list_sections,
    search_contract,
    search_policy,
    search_policy_by_categories,
    search_policy_fts,
    search_policy_recall,
)
from document_core.services.catalog_search import search_policy_catalog
from document_core.store.memory_store import get_store, set_store
from mcp.document_server.config import (
    MCP_CAPABILITIES,
    SERVICE_NAME,
    VERSION,
    get_settings,
    resolve_build_id,
)

logger = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    build_id: str = ""
    store_backend: str = "pgvector"
    db: str = "ok"
    capabilities: list[str] = Field(default_factory=list)


class ListPoliciesRequest(BaseModel):
    tenant_id: str
    kind: DocumentKind = DocumentKind.POLICY


class ListPoliciesResponse(BaseModel):
    tenant_id: str
    document_ids: list[str]


class ListPolicyIdsByCategoriesRequest(BaseModel):
    tenant_id: str
    categories: list[str] = Field(default_factory=list)
    contract_type: str | None = None


class ListPolicyIdsByCategoriesResponse(BaseModel):
    tenant_id: str
    document_ids: list[str]


class ToolResponse(BaseModel):
    request_id: str
    result: Any
    latency_ms: int


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    logger.info("service starting service=%s version=%s", SERVICE_NAME, VERSION)

    from document_core.config import get_settings as get_core_settings
    from document_core.db.migrate import run_migrations
    from document_core.store.async_adapter import AsyncDocumentStoreAdapter
    from document_core.store.pgvector_store import PgVectorDocumentStore

    core = get_core_settings()
    if not core.database_url:
        raise RuntimeError("DATABASE_URL is required for document-mcp")
    run_migrations(core.database_url)
    pg_store = PgVectorDocumentStore(
        core.database_url,
        hybrid_alpha=core.search_hybrid_alpha,
    )
    pg_store.ping()
    async_store = AsyncDocumentStoreAdapter(pg_store)
    set_store(async_store)
    logger.info("document store backend=pgvector (async adapter enabled)")

    yield
    logger.info("shutting down service=%s", SERVICE_NAME)


app = FastAPI(
    title="Document MCP Server",
    description="Contract and policy document ingest, RAG search, and grounding",
    version=VERSION,
    lifespan=lifespan,
)

def _request_id() -> str:
    return uuid.uuid4().hex[:12]


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    store = get_store()
    db_status = "ok"
    try:
        if hasattr(store, "ping_async"):
            ok = await store.ping_async()
        else:
            import asyncio

            ok = await asyncio.to_thread(store.ping)
        if not ok:
            db_status = "error"
    except Exception:  # noqa: BLE001
        db_status = "error"
    return HealthResponse(
        status="ok" if db_status == "ok" else "degraded",
        service=SERVICE_NAME,
        version=VERSION,
        build_id=resolve_build_id(),
        store_backend="pgvector",
        db=db_status,
        capabilities=list(MCP_CAPABILITIES),
    )


@app.post("/tools/ingest_document", response_model=IngestResult)
async def ingest_document_tool(request: IngestRequest) -> IngestResult:
    try:
        return await ingest_document(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/tools/index_policy", response_model=IngestResult)
async def index_policy_tool(request: IngestRequest) -> IngestResult:
    payload = request.model_copy(update={"kind": DocumentKind.POLICY})
    try:
        return await ingest_document(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/tools/register_policy", response_model=PolicyRegistryRecord)
async def register_policy_tool(request: RegisterPolicyRequest) -> PolicyRegistryRecord:
    return await register_policy_async(request)


@app.post("/tools/register_contract", response_model=PolicyRegistryRecord)
async def register_contract_tool(request: RegisterContractRequest) -> PolicyRegistryRecord:
    return await register_contract_async(request)


@app.post("/tools/get_policy_by_ref", response_model=PolicyRegistryRecord)
async def get_policy_by_ref_tool(request: GetPolicyByRefRequest) -> PolicyRegistryRecord:
    record = await get_policy_by_ref_async(request.tenant_id, request.policy_ref)
    if record is None:
        raise HTTPException(status_code=404, detail="policy not found")
    return record


@app.post("/tools/get_contract_by_ref", response_model=PolicyRegistryRecord)
async def get_contract_by_ref_tool(request: GetPolicyByRefRequest) -> PolicyRegistryRecord:
    record = await get_contract_by_ref_async(request.tenant_id, request.policy_ref)
    if record is None:
        raise HTTPException(status_code=404, detail="contract not found")
    return record


@app.post("/tools/delete_policy", response_model=DeletePolicyResult)
async def delete_policy_tool(request: DeletePolicyRequest) -> DeletePolicyResult:
    try:
        return await delete_policy_async(request)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/tools/list_policy_registry", response_model=ListPolicyRegistryResponse)
async def list_policy_registry_tool(
    request: ListPolicyRegistryRequest,
) -> ListPolicyRegistryResponse:
    return await list_policy_registry_async(request)


@app.post("/tools/search_contract")
async def search_contract_tool(request: SearchRequest) -> dict[str, Any]:
    hits = await search_contract(request)
    return {"results": [h.model_dump(mode="json") for h in hits]}


@app.post("/tools/search_policy")
async def search_policy_tool(request: SearchRequest) -> dict[str, Any]:
    hits = await search_policy(request)
    return {"results": [h.model_dump(mode="json") for h in hits]}


@app.post("/tools/search_policy_fts")
async def search_policy_fts_tool(request: SearchRequest) -> dict[str, Any]:
    hits = await search_policy_fts(request)
    return {"results": [h.model_dump(mode="json") for h in hits]}


@app.post("/tools/search_policy_recall")
async def search_policy_recall_tool(request: SearchRequest) -> dict[str, Any]:
    hits = await search_policy_recall(request)
    return {"results": [h.model_dump(mode="json") for h in hits]}


@app.post("/tools/search_policy_by_categories")
async def search_policy_by_categories_tool(request: SearchRequest) -> dict[str, Any]:
    # Categories travel in SearchRequest.metadata.categories (see document_client.py).
    categories = list((getattr(request, "metadata", None) or {}).get("categories") or [])
    if not categories:
        return {"results": []}
    hits = await search_policy_by_categories(
        request.tenant_id,
        list(categories),
        request.query,
        contract_type=request.contract_type,
        policy_type=request.policy_type,
        top_k=request.top_k,
    )
    return {"results": [h.model_dump(mode="json") for h in hits]}


@app.post("/tools/search_policy_catalog")
async def search_policy_catalog_tool(request: CatalogSearchRequest) -> dict[str, Any]:
    hits = await search_policy_catalog(request)
    return {"results": [h.model_dump(mode="json") for h in hits]}


@app.post("/tools/list_policy_ids_by_categories", response_model=ListPolicyIdsByCategoriesResponse)
async def list_policy_ids_by_categories_tool(
    request: ListPolicyIdsByCategoriesRequest,
) -> ListPolicyIdsByCategoriesResponse:
    doc_ids = await list_policy_ids_by_categories(
        request.tenant_id,
        list(request.categories),
        contract_type=request.contract_type,
    )
    return ListPolicyIdsByCategoriesResponse(
        tenant_id=request.tenant_id,
        document_ids=[str(doc_id) for doc_id in doc_ids],
    )


@app.post("/tools/list_sections")
async def list_sections_tool(request: ListSectionsRequest) -> dict[str, Any]:
    try:
        sections = await list_sections(request)
    except ValueError as exc:
        if str(exc) == "document deleted":
            raise HTTPException(status_code=404, detail="document deleted") from exc
        raise
    return {"sections": [s.model_dump(mode="json") for s in sections]}


@app.post("/tools/get_section")
async def get_section_tool(request: GetSectionRequest) -> IndexedChunk:
    section = await get_section(request)
    if section is None:
        raise HTTPException(status_code=404, detail="section not found")
    return section


@app.post("/tools/verify_quote", response_model=GroundingCheckResult)
async def verify_quote_tool(request: GroundingCheckRequest) -> GroundingCheckResult:
    return await verify_quote(request)


@app.post("/tools/verify_policy_quote", response_model=GroundingCheckResult)
async def verify_policy_quote_tool(request: GroundingCheckRequest) -> GroundingCheckResult:
    return await verify_quote(request)


@app.post("/tools/list_policies", response_model=ListPoliciesResponse)
async def list_policies_tool(request: ListPoliciesRequest) -> ListPoliciesResponse:
    store = get_store()
    if hasattr(store, "list_documents_async"):
        doc_ids = await store.list_documents_async(request.tenant_id, request.kind)
    else:
        import asyncio
        doc_ids = await asyncio.to_thread(store.list_documents, request.tenant_id, request.kind)
    return ListPoliciesResponse(
        tenant_id=request.tenant_id,
        document_ids=[str(doc_id) for doc_id in doc_ids],
    )


@app.middleware("http")
async def log_requests(request: Request, call_next):  # type: ignore[no-untyped-def]
    started = time.perf_counter()
    request_id = request.headers.get("x-request-id", _request_id())
    response = await call_next(request)
    latency_ms = int((time.perf_counter() - started) * 1000)
    response.headers["x-request-id"] = request_id
    if request.url.path.startswith("/tools/"):
        logger.info(
            "tool_call path=%s status=%s latency_ms=%s request_id=%s",
            request.url.path,
            response.status_code,
            latency_ms,
            request_id,
        )
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error path=%s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": str(exc)})
