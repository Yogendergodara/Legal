"""Orchestrates unified search across all data sources."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from mcp.retrieval_server.authority import apply_authority_metadata
from mcp.retrieval_server.config import Settings
from mcp.retrieval_server.integrations.legal_authority_search import LegalAuthoritySearchClient
from mcp.retrieval_server.integrations.internal_docs import InternalDocsClient
from mcp.retrieval_server.integrations.web_search import WebSearchClient
from mcp.retrieval_server.logging_setup import get_logger, truncate
from mcp.retrieval_server.models import SearchRequest, SearchResponse, SearchResult

logger = get_logger(__name__)


class AllSourcesFailedError(Exception):
    """Raised when every configured source fails for a search request."""


class SearchService:
    """Unified search orchestrator with fan-out, dedupe, and ranking."""

    def __init__(self, http_client: httpx.AsyncClient, settings: Settings) -> None:
        self._settings = settings
        self._web = WebSearchClient(http_client, settings)
        self._internal = InternalDocsClient(settings)
        self._legal_authority = LegalAuthoritySearchClient(
            call_timeout=settings.legal_authority_call_timeout,
            global_timeout=settings.legal_authority_global_timeout,
        )

    async def search(self, request: SearchRequest, request_id: str) -> SearchResponse:
        """Execute a unified search based on search_type."""
        start = time.perf_counter()

        if request.search_type == "all":
            results, degraded, source_failures = await self.search_all(request, request_id)
        else:
            results, degraded, source_failures = await self._search_single(request, request_id)

        if source_failures > 0 and len(results) == 0 and self._expected_source_count(request) == source_failures:
            logger.error(
                "all sources failed",
                request_id=request_id,
                search_type=request.search_type,
            )
            raise AllSourcesFailedError("All configured search sources failed")

        elapsed_ms = int((time.perf_counter() - start) * 1000)

        return SearchResponse(
            request_id=request_id,
            query=request.query,
            search_type=request.search_type,
            results=results,
            total_results=len(results),
            degraded=degraded,
            search_time_ms=elapsed_ms,
        )

    async def search_web(
        self,
        query: str,
        max_results: int,
        jurisdiction: str = "India",
        request_id: str = "-",
    ) -> tuple[list[SearchResult], bool]:
        """Search the web via the configured backend and map to SearchResult."""
        logger.info(
            "searching web",
            request_id=request_id,
            query=truncate(query, 200),
            max_results=max_results,
        )

        raw, degraded = await self._web.search(query, max_results, request_id)
        backend = self._settings.websearch_backend

        results: list[SearchResult] = []
        for idx, item in enumerate(raw):
            if not isinstance(item, dict):
                continue

            url = str(item.get("url", ""))
            title = str(item.get("title", "Untitled"))
            snippet = str(
                item.get("snippet") or item.get("description") or item.get("content") or ""
            )
            score = float(item.get("score", item.get("relevance_score", max(0.1, 1.0 - idx * 0.05))))
            engine = item.get("engine", backend)

            source_id = url if url else f"web:{hash(title) & 0xFFFFFFFF:08x}"
            results.append(
                SearchResult(
                    source_id=source_id,
                    source_type="web",
                    title=title,
                    text_snippet=truncate(snippet, 2000),
                    url=url,
                    jurisdiction=jurisdiction,
                    relevance_score=round(min(score, 1.0), 2),
                    metadata={"backend": backend, "engine": engine},
                )
            )

        logger.info(
            "web search mapped",
            request_id=request_id,
            count=len(results),
            degraded=degraded,
        )
        return results, degraded

    async def search_legal_authority(
        self,
        query: str,
        max_results: int,
        request_id: str = "-",
    ) -> tuple[list[SearchResult], bool]:
        """Search primary Indian legal sites via site-restricted web search."""
        raw, degraded = await self._legal_authority.search(query, max_results, request_id)
        results: list[SearchResult] = []
        for idx, item in enumerate(raw):
            if not isinstance(item, dict):
                continue
            url = str(item.get("url", ""))
            title = str(item.get("title", "Untitled"))
            snippet = str(item.get("snippet") or "")
            score = float(item.get("score", max(0.1, 1.0 - idx * 0.05)))
            metadata = item.get("metadata") or {}
            backend = str(metadata.get("backend") or "legal_authority")
            source_id = url if url else f"legal:{hash(title) & 0xFFFFFFFF:08x}"
            results.append(
                SearchResult(
                    source_id=source_id,
                    source_type="web",
                    title=title,
                    text_snippet=truncate(snippet, 2000),
                    url=url,
                    jurisdiction="India",
                    relevance_score=round(min(score, 1.0), 2),
                    metadata={"backend": backend, "engine": backend, **metadata},
                )
            )
        return results, degraded

    async def search_all(
        self, request: SearchRequest, request_id: str = "-"
    ) -> tuple[list[SearchResult], bool, int]:
        """Fan out to legal authority sites, general web, and optionally internal sources."""
        sources = ["legal_authority"]
        if not self._settings.search_skip_redundant_web:
            sources.append("web")
        include_internal = request.tenant_id is not None
        if include_internal:
            sources.append("internal")

        logger.info("fan-out started", sources=sources)

        if request.filters:
            logger.debug("filters applied", filters=request.filters)

        tasks: list[tuple[str, Any]] = []
        for source in sources:
            if source == "legal_authority":
                tasks.append(
                    (
                        "legal_authority",
                        self.search_legal_authority(
                            request.query, request.max_results, request_id
                        ),
                    )
                )
            elif source == "web":
                tasks.append(
                    ("web", self.search_web(request.query, request.max_results, request.jurisdiction, request_id))
                )
            elif source == "internal" and request.tenant_id:
                tasks.append(
                    ("internal", self._internal.search(request.query, request.tenant_id, request.max_results, request.jurisdiction))
                )

        source_names = [name for name, _ in tasks]
        coroutines = [coro for _, coro in tasks]

        source_starts = {name: time.perf_counter() for name in source_names}
        gathered = await asyncio.gather(*coroutines, return_exceptions=True)

        all_results: list[SearchResult] = []
        degraded = False
        source_failures = 0

        for source_name, outcome in zip(source_names, gathered):
            source_duration_ms = int((time.perf_counter() - source_starts[source_name]) * 1000)

            if isinstance(outcome, Exception):
                degraded = True
                source_failures += 1
                logger.warning(
                    "external api failed",
                    source=source_name,
                    error=type(outcome).__name__,
                    message=str(outcome),
                    duration_ms=source_duration_ms,
                    action="skipped_source",
                )
                logger.info(
                    "source returned results",
                    source=source_name,
                    count=0,
                    duration_ms=source_duration_ms,
                )
                continue

            if isinstance(outcome, tuple):
                source_results, source_degraded = outcome
                if source_degraded:
                    degraded = True
                count = len(source_results)
                logger.info(
                    "source returned results",
                    source=source_name,
                    count=count,
                    duration_ms=source_duration_ms,
                )
                all_results.extend(source_results)
                continue

            count = len(outcome)
            logger.info(
                "source returned results",
                source=source_name,
                count=count,
                duration_ms=source_duration_ms,
            )
            all_results.extend(outcome)

        final = self._dedupe_and_rank(all_results, request.max_results)
        return final, degraded, source_failures

    async def _search_single(
        self, request: SearchRequest, request_id: str = "-"
    ) -> tuple[list[SearchResult], bool, int]:
        """Search a single source type."""
        source = request.search_type
        degraded = False
        source_failures = 0

        if source == "internal" and not request.tenant_id:
            logger.warning(
                "internal search called without tenant_id",
                action="returning_empty",
            )
            return [], False, 0

        if request.filters:
            logger.debug("filters applied", filters=request.filters)

        start = time.perf_counter()
        try:
            if source == "web":
                results, web_degraded = await self.search_web(
                    request.query,
                    request.max_results,
                    request.jurisdiction,
                    request_id,
                )
                if web_degraded:
                    degraded = True
            elif source == "internal":
                results = await self._internal.search(
                    request.query,
                    request.tenant_id,  # type: ignore[arg-type]
                    request.max_results,
                    request.jurisdiction,
                )
            else:
                results = []
        except Exception as exc:
            degraded = True
            source_failures = 1
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.warning(
                "external api failed",
                source=source,
                error=type(exc).__name__,
                message=str(exc),
                duration_ms=duration_ms,
                action="skipped_source",
            )
            results = []

        duration_ms = int((time.perf_counter() - start) * 1000)
        logger.info(
            "source returned results",
            source=source,
            count=len(results),
            duration_ms=duration_ms,
        )

        final = self._dedupe_and_rank(results, request.max_results)
        return final, degraded, source_failures

    def _dedupe_and_rank(
        self, results: list[SearchResult], max_results: int
    ) -> list[SearchResult]:
        """Deduplicate by source_id, sort by relevance_score desc, truncate."""
        raw_count = len(results)

        seen: dict[str, SearchResult] = {}
        for result in results:
            existing = seen.get(result.source_id)
            if existing is None or result.relevance_score > existing.relevance_score:
                seen[result.source_id] = result

        unique = list(seen.values())
        unique_count = len(unique)

        logger.info("dedupe complete", raw=raw_count, unique=unique_count)

        boosted = [apply_authority_metadata(result) for result in unique]
        ranked = sorted(boosted, key=lambda r: r.relevance_score, reverse=True)
        final = ranked[:max_results]

        logger.info("ranking complete", returned=len(final))

        for rank, result in enumerate(final, start=1):
            logger.debug(
                "result",
                rank=rank,
                title=result.title,
                score=result.relevance_score,
                type=result.source_type,
            )

        return final

    def _expected_source_count(self, request: SearchRequest) -> int:
        """Return how many sources are expected for this request."""
        if request.search_type == "all":
            count = 1  # legal authority (always)
            if not self._settings.search_skip_redundant_web:
                count += 1  # general web
            if request.tenant_id:
                count += 1
            return count
        return 1
