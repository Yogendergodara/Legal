"""HTTP client for the Legal ai Retrieval MCP server."""

from __future__ import annotations

import asyncio
import os
from typing import Any, Literal

import httpx

from deep_research_from_scratch.config import config

SearchType = Literal["web", "internal", "all"]
SearchDepth = Literal["normal", "deep"]


class MCPClientError(Exception):
    """Raised when a retrieval MCP tool call fails after retries."""


class RetrievalMCPClient:
    """Client for the retrieval server's /tools/* HTTP endpoints."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
    ) -> None:
        resolved_url = base_url or os.environ.get(
            "RETRIEVAL_SERVER_URL", config.RETRIEVAL_SERVER_URL
        )
        self.base_url = resolved_url.rstrip("/")
        self.timeout_seconds = (
            timeout_seconds if timeout_seconds is not None else config.RETRIEVAL_TIMEOUT_SECONDS
        )
        self.max_retries = max_retries if max_retries is not None else config.RETRIEVAL_MAX_RETRIES

    async def search(
        self,
        query: str,
        *,
        search_type: SearchType = "all",
        jurisdiction: str = "India",
        max_results: int = 10,
        tenant_id: str | None = None,
        filters: dict[str, Any] | None = None,
        search_depth: SearchDepth = "normal",
    ) -> list[dict[str, Any]]:
        """Unified keyword search across web and internal legal sources."""
        payload: dict[str, Any] = {
            "query": query,
            "search_type": search_type,
            "jurisdiction": jurisdiction,
            "max_results": max_results,
            "search_depth": search_depth,
        }
        if tenant_id:
            payload["tenant_id"] = tenant_id
        if filters:
            payload["filters"] = filters

        data = await self._post("/tools/search", payload)
        return list(data.get("results", []))

    async def fetch(self, url: str) -> dict[str, Any]:
        """Fetch and extract clean text from a web URL via the MCP server."""
        payload = {"source_id": url, "source_type": "web"}
        return await self._post("/tools/fetch_and_extract", payload)

    async def save_memory(
        self, title: str, content: str, hook: str = ""
    ) -> dict[str, Any]:
        """Persist a durable legal fact to long-term memory on the MCP server."""
        payload = {"title": title, "content": content, "hook": hook}
        return await self._post("/tools/memory/save", payload)

    async def search_memory(self, query: str) -> list[dict[str, Any]]:
        """Search long-term memory on the MCP server for matching saved facts."""
        data = await self._post("/tools/memory/search", {"query": query})
        return list(data.get("results", []))

    async def semantic_search(
        self,
        query: str,
        *,
        top_k: int = 10,
        threshold: float = 0.7,
        search_type: str = "all",
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Vector search over indexed legal documents."""
        payload: dict[str, Any] = {
            "query": query,
            "search_type": search_type,
            "top_k": top_k,
            "threshold": threshold,
        }
        if tenant_id:
            payload["tenant_id"] = tenant_id
        data = await self._post("/tools/semantic_search", payload)
        if data.get("stub"):
            return []
        results = data.get("results", [])
        normalized: list[dict[str, Any]] = []
        for hit in results:
            if not isinstance(hit, dict):
                continue
            url = hit.get("url") or hit.get("source_id") or ""
            normalized.append(
                {
                    "title": hit.get("title", "Untitled"),
                    "url": url if str(url).startswith("http") else "",
                    "text_snippet": hit.get("text_snippet", ""),
                    "similarity_score": hit.get("similarity_score", 0.0),
                    "metadata": {
                        **(hit.get("metadata") or {}),
                        "backend": "semantic",
                    },
                }
            )
        return normalized

    async def _post(self, tool_path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{tool_path}"
        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(self.timeout_seconds)
                ) as client:
                    response = await client.post(url, json=payload)
                    response.raise_for_status()
                    return response.json()
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt < self.max_retries:
                    await asyncio.sleep(min(0.5 * attempt, 2.0))

        raise MCPClientError(
            f"Retrieval MCP tool '{tool_path.rsplit('/', 1)[-1]}' failed after "
            f"{self.max_retries} attempts: {last_error}"
        ) from last_error

    async def health(self) -> dict[str, Any]:
        """Check server health."""
        async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_seconds)) as client:
            response = await client.get(f"{self.base_url}/health")
            response.raise_for_status()
            return response.json()


_default_client: RetrievalMCPClient | None = None


def get_retrieval_client() -> RetrievalMCPClient:
    """Return the process-wide retrieval MCP client."""
    global _default_client  # noqa: PLW0603
    if _default_client is None:
        _default_client = RetrievalMCPClient()
    return _default_client
