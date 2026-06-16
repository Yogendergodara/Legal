"""Self-hosted web search client — DuckDuckGo, open-webSearch, or legal-index."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from mcp.retrieval_server.config import Settings
from mcp.retrieval_server.logging_setup import get_logger, truncate

logger = get_logger(__name__)


def _extract_search_results(payload: object) -> list[dict[str, Any]]:
    """Normalize open-webSearch response shapes into a result list."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if not isinstance(payload, dict):
        return []

    for key in ("results", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]

    nested = payload.get("data")
    if isinstance(nested, list):
        return [item for item in nested if isinstance(item, dict)]
    if isinstance(nested, dict):
        for key in ("results", "items"):
            value = nested.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]

    return []


def _duckduckgo_search_sync(
    query: str, max_results: int, timeout: float = 8.0
) -> list[dict[str, Any]]:
    """Run DuckDuckGo text search (sync — called from a thread pool).

    ``timeout`` is forwarded to the DDGS HTTP client so individual engine
    requests do not stall indefinitely.  The asyncio-level ``wait_for`` in
    callers provides the outer hard cap.
    """
    from ddgs import DDGS

    raw = DDGS(timeout=timeout).text(query, max_results=max_results, region="in-en")
    results: list[dict[str, Any]] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        results.append(
            {
                "url": item.get("href") or item.get("url") or "",
                "title": item.get("title") or "Untitled",
                "snippet": item.get("body") or item.get("snippet") or "",
                "score": max(0.1, 1.0 - idx * 0.05),
                "engine": "duckduckgo",
            }
        )
    return results


class WebSearchClient:
    """
    Calls a configured search backend.
    - duckduckgo: direct web search, no Docker/API key (default for local dev)
    - open-websearch: self-hosted daemon (Docker / VPC)
    - legal-index: Postgres FTS over crawled legal documents
    """

    def __init__(self, http_client: httpx.AsyncClient, settings: Settings) -> None:
        self._client = http_client
        self._settings = settings
        self._base_url = settings.websearch_base_url.rstrip("/")
        self._timeout = settings.external_timeout_seconds
        self._backend = settings.websearch_backend

        logger.info(
            "web_search client configured",
            backend=self._backend,
            base_url=self._base_url if self._backend == "open-websearch" else None,
        )

    async def search(
        self,
        query: str,
        max_results: int,
        request_id: str = "-",
    ) -> tuple[list[dict[str, Any]], bool]:
        """
        Search the web via the configured backend.
        Returns (raw_results, degraded). Never raises — failures return ([], True).
        """
        if self._backend == "legal-index":
            return await self._search_legal_index(query, max_results, request_id)
        if self._backend == "duckduckgo":
            return await self._search_duckduckgo(query, max_results, request_id)
        return await self._search_open_websearch(query, max_results, request_id)

    async def _search_duckduckgo(
        self,
        query: str,
        max_results: int,
        request_id: str,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Search via DuckDuckGo (no API key, works without Docker)."""
        logger.info(
            "calling search backend",
            request_id=request_id,
            source="duckduckgo",
            query=truncate(query, 200),
            limit=max_results,
        )

        start = time.perf_counter()
        try:
            results = await asyncio.wait_for(
                asyncio.to_thread(_duckduckgo_search_sync, query, max_results, self._timeout),
                timeout=self._timeout,
            )
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.info(
                "search backend responded",
                request_id=request_id,
                source="duckduckgo",
                count=len(results),
                duration_ms=duration_ms,
            )
            return results, False

        except asyncio.TimeoutError:
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.warning(
                "search backend timeout",
                request_id=request_id,
                source="duckduckgo",
                duration_ms=duration_ms,
                action="skipped_source",
            )
            return [], True

        except Exception as exc:
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.error(
                "search backend failed",
                request_id=request_id,
                source="duckduckgo",
                error=type(exc).__name__,
                message=str(exc),
                duration_ms=duration_ms,
                exc_info=True,
            )
            return [], True

    async def _search_open_websearch(
        self,
        query: str,
        max_results: int,
        request_id: str,
    ) -> tuple[list[dict[str, Any]], bool]:
        """POST to self-hosted open-webSearch daemon."""
        url = f"{self._base_url}/search"
        payload = {"query": query, "limit": max_results}

        logger.info(
            "calling internal search backend",
            request_id=request_id,
            source="open-websearch",
            url=url,
            query=truncate(query, 200),
            limit=max_results,
        )

        start = time.perf_counter()
        try:
            response = await self._client.post(
                url,
                json=payload,
                timeout=self._timeout,
            )
            duration_ms = int((time.perf_counter() - start) * 1000)

            if response.status_code != 200:
                logger.warning(
                    "internal search backend non-200",
                    request_id=request_id,
                    source="open-websearch",
                    status=response.status_code,
                    duration_ms=duration_ms,
                    action="returning_empty",
                )
                return [], True

            data = response.json()
            results = _extract_search_results(data)

            logger.info(
                "internal search backend responded",
                request_id=request_id,
                source="open-websearch",
                status=200,
                count=len(results),
                duration_ms=duration_ms,
            )
            return results, False

        except httpx.TimeoutException:
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.warning(
                "internal search backend timeout",
                request_id=request_id,
                source="open-websearch",
                duration_ms=duration_ms,
                action="skipped_source",
            )
            return [], True

        except Exception as exc:
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.error(
                "internal search backend failed",
                request_id=request_id,
                source="open-websearch",
                error=type(exc).__name__,
                message=str(exc),
                duration_ms=duration_ms,
                exc_info=True,
            )
            return [], True

    async def _search_legal_index(
        self,
        query: str,
        max_results: int,
        request_id: str,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Query Postgres full-text search over crawled legal web documents."""
        from crawler.fts import search_documents

        logger.info(
            "calling internal search backend",
            request_id=request_id,
            source="legal-index",
            query=truncate(query, 200),
            limit=max_results,
        )

        start = time.perf_counter()
        try:
            results = await search_documents(
                query=query,
                limit=max_results,
                database_url=self._settings.database_url,
            )
            duration_ms = int((time.perf_counter() - start) * 1000)

            logger.info(
                "internal search backend responded",
                request_id=request_id,
                source="legal-index",
                status=200,
                count=len(results),
                duration_ms=duration_ms,
            )
            return results, False

        except Exception as exc:
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.error(
                "internal search backend failed",
                request_id=request_id,
                source="legal-index",
                error=type(exc).__name__,
                message=str(exc),
                duration_ms=duration_ms,
                exc_info=True,
            )
            return [], True
