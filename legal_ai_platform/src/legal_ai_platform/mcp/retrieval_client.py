"""HTTP client for the Legal ai Retrieval MCP Server."""

from __future__ import annotations

from typing import Any, Literal

from legal_ai_platform.mcp.base_client import BaseMCPClient
from legal_ai_platform.models.retrieval import (
    CitationGraphResult,
    FetchResult,
    RetrievalResult,
)

SearchType = Literal["web", "internal", "all"]
SourceType = Literal["web", "internal"]
CitationDirection = Literal["incoming", "outgoing", "both"]


class RetrievalMCPClient(BaseMCPClient):
    """Client for the retrieval server's /tools/* endpoints.

    All methods normalize responses to platform domain models so future agents
    consume a single ``RetrievalResult`` schema regardless of the underlying tool.
    """

    server_name = "retrieval-mcp"

    async def search(
        self,
        query: str,
        *,
        search_type: SearchType = "all",
        jurisdiction: str = "India",
        max_results: int = 10,
        tenant_id: str | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[RetrievalResult]:
        """Unified keyword search across web and internal docs."""
        payload: dict[str, Any] = {
            "query": query,
            "search_type": search_type,
            "jurisdiction": jurisdiction,
            "max_results": max_results,
        }
        if tenant_id:
            payload["tenant_id"] = tenant_id
        if filters:
            payload["filters"] = filters

        data = await self._post("/tools/search", payload)
        return [RetrievalResult.from_search_hit(hit) for hit in data.get("results", [])]

    async def search_notifications(
        self,
        query: str,
        *,
        max_results: int = 10,
        jurisdiction: str = "India",
    ) -> list[RetrievalResult]:
        """Search government notifications via web index."""
        return await self.search(
            query,
            search_type="web",
            jurisdiction=jurisdiction,
            max_results=max_results,
            filters={"content_type": "notification"},
        )

    async def semantic_search(
        self,
        query: str,
        *,
        search_type: SearchType = "all",
        top_k: int = 10,
        threshold: float = 0.7,
        tenant_id: str | None = None,
    ) -> list[RetrievalResult]:
        """Vector semantic search."""
        payload: dict[str, Any] = {
            "query": query,
            "search_type": search_type,
            "top_k": top_k,
            "threshold": threshold,
        }
        if tenant_id:
            payload["tenant_id"] = tenant_id

        data = await self._post("/tools/semantic_search", payload)
        return [RetrievalResult.from_search_hit(hit) for hit in data.get("results", [])]

    async def fetch_and_extract(
        self,
        source_id: str,
        source_type: SourceType,
        *,
        extract_sections: list[str] | None = None,
        tenant_id: str | None = None,
    ) -> FetchResult:
        """Fetch and extract a full document."""
        payload: dict[str, Any] = {
            "source_id": source_id,
            "source_type": source_type,
        }
        if extract_sections:
            payload["extract_sections"] = extract_sections
        if tenant_id:
            payload["tenant_id"] = tenant_id

        data = await self._post("/tools/fetch_and_extract", payload)
        return FetchResult(
            source_id=data.get("source_id", source_id),
            source_type=data.get("source_type", source_type),
            title=data.get("title", ""),
            full_text=data.get("full_text", ""),
            url=data.get("url", ""),
            sections=[
                {"section_id": s.get("section_id", ""), "title": s.get("title", ""), "content": s.get("content", "")}
                for s in data.get("sections", [])
            ],
            metadata=data.get("metadata") or {},
            fetch_time_ms=data.get("fetch_time_ms", 0),
        )

    async def citation_graph(
        self,
        source_id: str,
        source_type: SourceType,
        *,
        depth: int = 1,
        direction: CitationDirection = "both",
    ) -> CitationGraphResult:
        """Retrieve a citation graph for a legal source."""
        payload = {
            "source_id": source_id,
            "source_type": source_type,
            "depth": depth,
            "direction": direction,
        }
        data = await self._post("/tools/citation_graph", payload)
        return CitationGraphResult(
            source_id=data.get("source_id", source_id),
            nodes=data.get("nodes", []),
            edges=data.get("edges", []),
            depth=data.get("depth", depth),
            direction=data.get("direction", direction),
            stub=data.get("stub", False),
            stub_reason=data.get("stub_reason"),
            graph_time_ms=data.get("graph_time_ms", 0),
        )
