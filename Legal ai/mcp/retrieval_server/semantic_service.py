"""Semantic vector search over web and tenant documents."""

from __future__ import annotations

import time

from mcp.retrieval_server.config import Settings
from mcp.retrieval_server.embedding_service import embed_text
from mcp.retrieval_server.logging_setup import get_logger, truncate
from mcp.retrieval_server.models import (
    SemanticSearchRequest,
    SemanticSearchResponse,
    SemanticSearchResult,
)
from db.search import semantic_search_tenant, semantic_search_web

logger = get_logger(__name__)


class SemanticSearchService:
    """Vector-based semantic search using pgvector."""

    def __init__(self, settings: Settings | None = None) -> None:
        from mcp.retrieval_server.config import get_settings

        self._settings = settings or get_settings()

    async def semantic_search(
        self, request: SemanticSearchRequest, request_id: str
    ) -> SemanticSearchResponse:
        start = time.perf_counter()

        logger.info(
            "semantic search started",
            request_id=request_id,
            query=truncate(request.query, 200),
            search_type=request.search_type,
            top_k=request.top_k,
            threshold=request.threshold,
            tenant_id=request.tenant_id,
        )

        try:
            query_vec = await embed_text(request.query)
            raw: list[dict] = []

            if request.search_type in ("web", "all"):
                raw.extend(
                    await semantic_search_web(
                        query_vec,
                        request.top_k,
                        self._settings.database_url,
                        request.threshold,
                    )
                )

            if request.search_type in ("internal", "all") and request.tenant_id:
                raw.extend(
                    await semantic_search_tenant(
                        query_vec,
                        request.tenant_id,
                        request.top_k,
                        self._settings.database_url,
                        request.threshold,
                    )
                )

            raw.sort(key=lambda x: x.get("similarity_score", 0), reverse=True)
            raw = raw[: request.top_k]

            results = [
                SemanticSearchResult(
                    source_id=item["source_id"],
                    source_type=item["source_type"],
                    title=item["title"],
                    text_snippet=truncate(item.get("text_snippet", ""), 200),
                    similarity_score=round(float(item["similarity_score"]), 4),
                    metadata={"backend": "pgvector"},
                )
                for item in raw
            ]

            elapsed_ms = int((time.perf_counter() - start) * 1000)
            logger.info(
                "semantic search complete",
                request_id=request_id,
                count=len(results),
                duration_ms=elapsed_ms,
            )

            return SemanticSearchResponse(
                request_id=request_id,
                query=request.query,
                results=results,
                total_results=len(results),
                search_time_ms=elapsed_ms,
                stub=False,
            )

        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            logger.error(
                "semantic search failed",
                request_id=request_id,
                error=type(exc).__name__,
                message=str(exc),
                duration_ms=elapsed_ms,
                exc_info=True,
            )
            return SemanticSearchResponse(
                request_id=request_id,
                query=request.query,
                results=[],
                total_results=0,
                search_time_ms=elapsed_ms,
                stub=True,
                stub_reason=f"{type(exc).__name__}: {exc}",
            )
