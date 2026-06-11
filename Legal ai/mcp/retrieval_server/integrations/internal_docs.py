"""Tenant-scoped internal document search."""

from __future__ import annotations

import time
from pathlib import Path

from mcp.retrieval_server.config import Settings
from mcp.retrieval_server.embedding_service import embed_text
from mcp.retrieval_server.integrations import internal_file_store
from mcp.retrieval_server.logging_setup import get_logger, truncate
from mcp.retrieval_server.models import SearchResult
from db.search import hybrid_search_tenant

logger = get_logger(__name__)


class InternalDocsClient:
    """Search tenant internal documents via hybrid FTS + vector or file store."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._file_root = (
            Path(settings.internal_storage_dir)
            if settings.internal_storage_dir
            else None
        )

    async def search(
        self,
        query: str,
        tenant_id: str,
        max_results: int,
        jurisdiction: str = "India",
    ) -> list[SearchResult]:
        start = time.perf_counter()

        logger.info(
            "internal docs search started",
            tenant_id=tenant_id,
            query=truncate(query, 200),
            max_results=max_results,
            storage=self._settings.internal_storage,
        )

        try:
            if self._settings.internal_storage == "file":
                raw = internal_file_store.search_documents(
                    query=query,
                    tenant_id=tenant_id,
                    max_results=max_results,
                    root=self._file_root,
                )
            else:
                query_vec = await embed_text(query)
                raw = await hybrid_search_tenant(
                    query=query,
                    query_vec=query_vec,
                    tenant_id=tenant_id,
                    limit=max_results,
                    database_url=self._settings.database_url,
                    alpha=self._settings.semantic_hybrid_alpha,
                )

            results: list[SearchResult] = []
            for idx, item in enumerate(raw):
                score = float(item.get("score", max(0.1, 1.0 - idx * 0.05)))
                results.append(
                    SearchResult(
                        source_id=item["source_id"],
                        source_type="internal",
                        title=item["title"],
                        text_snippet=truncate(item.get("text_snippet", ""), 200),
                        url="",
                        jurisdiction=jurisdiction,
                        relevance_score=round(min(score, 1.0), 2),
                        metadata={
                            "backend": "tenant_documents",
                            "tenant_id": tenant_id,
                            "storage": self._settings.internal_storage,
                        },
                    )
                )

            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.info(
                "internal docs search completed",
                tenant_id=tenant_id,
                count=len(results),
                duration_ms=duration_ms,
            )
            return results

        except Exception as exc:
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.error(
                "internal docs search failed",
                tenant_id=tenant_id,
                error=type(exc).__name__,
                message=str(exc),
                duration_ms=duration_ms,
                exc_info=True,
            )
            return []
