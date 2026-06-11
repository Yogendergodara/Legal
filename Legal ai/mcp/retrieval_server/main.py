"""FastAPI application for the Retrieval MCP server."""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from mcp.retrieval_server.citation_service import CitationService
from mcp.retrieval_server.config import SERVICE_NAME, VERSION, get_settings
from mcp.retrieval_server.fetch_service import FetchService
from mcp.retrieval_server.ingest_service import IngestService
from mcp.retrieval_server.logging_setup import (
    bind_request,
    clear_request_context,
    configure_logging,
    get_logger,
    truncate,
)
from mcp.retrieval_server.models import (
    CitationGraphRequest,
    CitationGraphResponse,
    FetchRequest,
    FetchResponse,
    HealthResponse,
    IngestInternalRequest,
    IngestInternalResponse,
    SearchRequest,
    SearchResponse,
    SemanticSearchRequest,
    SemanticSearchResponse,
    utc_now,
)
from mcp.retrieval_server.search_service import AllSourcesFailedError, SearchService
from mcp.retrieval_server.semantic_service import SemanticSearchService

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application startup and shutdown lifecycle."""
    settings = get_settings()
    configure_logging(settings.log_level)

    logger.info(
        "service starting",
        service=SERVICE_NAME,
        version=VERSION,
        log_level=settings.log_level,
        websearch_backend=settings.websearch_backend,
        websearch_base_url=settings.websearch_base_url,
        external_timeout_seconds=settings.external_timeout_seconds,
    )

    http_client = httpx.AsyncClient()
    app.state.http_client = http_client
    app.state.settings = settings
    app.state.search_service = SearchService(http_client, settings)
    app.state.fetch_service = FetchService(http_client, settings)
    app.state.semantic_service = SemanticSearchService(settings)
    app.state.citation_service = CitationService(settings, http_client)
    app.state.ingest_service = IngestService(settings)

    yield

    logger.info("shutting down", service=SERVICE_NAME)
    await http_client.aclose()


app = FastAPI(
    title="Retrieval MCP Server",
    description="Legal research tools for Indian Legal AI platform",
    version=VERSION,
    lifespan=lifespan,
)


def _new_request_id() -> str:
    return uuid.uuid4().hex[:12]


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Health check endpoint."""
    return HealthResponse(
        status="ok",
        service=SERVICE_NAME,
        version=VERSION,
        timestamp=utc_now(),
    )


@app.post("/tools/search", response_model=SearchResponse)
async def search_tool(request: Request, body: SearchRequest) -> SearchResponse:
    """Unified legal research search across multiple sources."""
    request_id = _new_request_id()
    bind_request(request_id)
    start = time.perf_counter()

    logger.info(
        "request received",
        tool="search",
        query=truncate(body.query, 200),
        search_type=body.search_type,
        jurisdiction=body.jurisdiction,
        max_results=body.max_results,
        tenant_id=body.tenant_id,
        filters=body.filters,
    )

    try:
        service: SearchService = request.app.state.search_service
        response = await service.search(body, request_id)

        duration_ms = int((time.perf_counter() - start) * 1000)
        logger.info(
            "request completed",
            tool="search",
            returned=response.total_results,
            duration_ms=duration_ms,
            degraded=response.degraded,
        )
        return response

    except AllSourcesFailedError as exc:
        duration_ms = int((time.perf_counter() - start) * 1000)
        logger.error(
            "request failed",
            tool="search",
            error=type(exc).__name__,
            message=str(exc),
            duration_ms=duration_ms,
            exc_info=True,
        )
        raise HTTPException(status_code=502, detail="All search sources failed") from exc

    except Exception as exc:
        duration_ms = int((time.perf_counter() - start) * 1000)
        logger.error(
            "request failed",
            tool="search",
            error=type(exc).__name__,
            message=str(exc),
            duration_ms=duration_ms,
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Internal server error") from exc

    finally:
        clear_request_context()


@app.post("/tools/fetch_and_extract", response_model=FetchResponse)
async def fetch_and_extract_tool(
    request: Request, body: FetchRequest
) -> FetchResponse:
    """Fetch full document and extract sections."""
    request_id = _new_request_id()
    bind_request(request_id)
    start = time.perf_counter()

    logger.info(
        "request received",
        tool="fetch_and_extract",
        source_id=body.source_id,
        source_type=body.source_type,
        extract_sections=body.extract_sections,
        tenant_id=body.tenant_id,
    )

    try:
        service: FetchService = request.app.state.fetch_service
        response = await service.fetch_and_extract(body, request_id)

        duration_ms = int((time.perf_counter() - start) * 1000)
        logger.info(
            "request completed",
            tool="fetch_and_extract",
            sections=len(response.sections),
            duration_ms=duration_ms,
        )
        return response

    except Exception as exc:
        duration_ms = int((time.perf_counter() - start) * 1000)
        logger.error(
            "request failed",
            tool="fetch_and_extract",
            error=type(exc).__name__,
            message=str(exc),
            duration_ms=duration_ms,
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Internal server error") from exc

    finally:
        clear_request_context()


@app.post("/tools/semantic_search", response_model=SemanticSearchResponse)
async def semantic_search_tool(
    request: Request, body: SemanticSearchRequest
) -> SemanticSearchResponse:
    """Semantic vector search — Phase 1 stub."""
    request_id = _new_request_id()
    bind_request(request_id)
    start = time.perf_counter()

    logger.info(
        "request received",
        tool="semantic_search",
        query=truncate(body.query, 200),
        search_type=body.search_type,
        top_k=body.top_k,
        threshold=body.threshold,
        tenant_id=body.tenant_id,
    )

    try:
        service: SemanticSearchService = request.app.state.semantic_service
        response = await service.semantic_search(body, request_id)

        duration_ms = int((time.perf_counter() - start) * 1000)
        logger.info(
            "request completed",
            tool="semantic_search",
            returned=response.total_results,
            duration_ms=duration_ms,
            stub=response.stub,
        )
        return response

    except Exception as exc:
        duration_ms = int((time.perf_counter() - start) * 1000)
        logger.error(
            "request failed",
            tool="semantic_search",
            error=type(exc).__name__,
            message=str(exc),
            duration_ms=duration_ms,
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Internal server error") from exc

    finally:
        clear_request_context()


@app.post("/tools/citation_graph", response_model=CitationGraphResponse)
async def citation_graph_tool(
    request: Request, body: CitationGraphRequest
) -> CitationGraphResponse:
    """Citation graph traversal — Phase 1 stub."""
    request_id = _new_request_id()
    bind_request(request_id)
    start = time.perf_counter()

    logger.info(
        "request received",
        tool="citation_graph",
        source_id=body.source_id,
        source_type=body.source_type,
        depth=body.depth,
        direction=body.direction,
    )

    try:
        service: CitationService = request.app.state.citation_service
        response = await service.citation_graph(body, request_id)

        duration_ms = int((time.perf_counter() - start) * 1000)
        logger.info(
            "request completed",
            tool="citation_graph",
            nodes=len(response.nodes),
            edges=len(response.edges),
            duration_ms=duration_ms,
            stub=response.stub,
        )
        return response

    except Exception as exc:
        duration_ms = int((time.perf_counter() - start) * 1000)
        logger.error(
            "request failed",
            tool="citation_graph",
            error=type(exc).__name__,
            message=str(exc),
            duration_ms=duration_ms,
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Internal server error") from exc

    finally:
        clear_request_context()


@app.post("/tools/ingest_internal", response_model=IngestInternalResponse)
async def ingest_internal_tool(
    request: Request, body: IngestInternalRequest
) -> IngestInternalResponse:
    """Ingest a tenant-scoped internal document into the index."""
    request_id = _new_request_id()
    bind_request(request_id)
    start = time.perf_counter()

    logger.info(
        "request received",
        tool="ingest_internal",
        tenant_id=body.tenant_id,
        title=truncate(body.title, 100),
        source_id=body.source_id,
    )

    try:
        service: IngestService = request.app.state.ingest_service
        result = await service.ingest_internal(
            tenant_id=body.tenant_id,
            title=body.title,
            doc_text=body.text,
            source_id=body.source_id,
            metadata=body.metadata,
            request_id=request_id,
        )

        duration_ms = int((time.perf_counter() - start) * 1000)
        logger.info(
            "request completed",
            tool="ingest_internal",
            source_id=result["source_id"],
            deduped=result["deduped"],
            duration_ms=duration_ms,
        )

        return IngestInternalResponse(
            request_id=request_id,
            tenant_id=result["tenant_id"],
            source_id=result["source_id"],
            title=result["title"],
            deduped=result["deduped"],
            ingest_time_ms=result["ingest_time_ms"],
        )

    except Exception as exc:
        duration_ms = int((time.perf_counter() - start) * 1000)
        logger.error(
            "request failed",
            tool="ingest_internal",
            error=type(exc).__name__,
            message=str(exc),
            duration_ms=duration_ms,
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Internal server error") from exc

    finally:
        clear_request_context()


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Return consistent JSON error responses."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )
