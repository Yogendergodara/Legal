"""Bridge between async RetrievalMCPClient and sync LangGraph search tools."""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
from collections.abc import Callable
from typing import Any

from deep_research_from_scratch.config import config
from deep_research_from_scratch.mcp_client import RetrievalMCPClient, get_retrieval_client
from deep_research_from_scratch.source_registry import (
    RetrievedSource,
    source_from_fetch,
    sources_from_search_hits,
)

_tenant_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("tenant_id", default=None)


def set_request_context(*, tenant_id: str | None = None) -> None:
    """Set per-request retrieval context (tenant_id for internal doc search)."""
    _tenant_id.set(tenant_id)


def get_tenant_id() -> str | None:
    """Return the tenant_id for the current request, if any."""
    return _tenant_id.get()


def format_retrieval_results(results: list[dict[str, Any]]) -> str:
    """Format retrieval MCP search hits for the research agent."""
    if not results:
        return (
            "No valid search results found. Please try different search queries "
            "or confirm the Legal ai retrieval MCP server is running."
        )

    formatted = "Search results: \n\n"
    for index, result in enumerate(results, 1):
        title = result.get("title", "Untitled")
        formatted += f"\n\n--- SOURCE {index}: {title} ---\n"
        url = result.get("url")
        if url:
            formatted += f"URL: {url}\n"
        metadata = result.get("metadata") or {}
        citation = metadata.get("citation")
        tier = metadata.get("authority_tier")
        if citation:
            formatted += f"CITATION: {citation}\n"
        if tier:
            formatted += f"AUTHORITY_TIER: {tier}\n"
        snippet = result.get("text_snippet", "")
        formatted += f"\nSUMMARY:\n{snippet}\n\n"
        formatted += "-" * 80 + "\n"
    return formatted


def format_semantic_results(results: list[dict[str, Any]], *, unavailable: bool = False) -> str:
    """Format semantic search hits for the research agent."""
    if unavailable:
        return (
            "Semantic search unavailable (Postgres/pgvector may be down or unindexed). "
            "Use web_search instead."
        )
    if not results:
        return (
            "No semantic search results found. Try web_search with a more specific query "
            "or fetch_url on a known statute/judgment URL."
        )

    formatted = "Semantic search results:\n\n"
    for index, result in enumerate(results, 1):
        title = result.get("title", "Untitled")
        formatted += f"\n\n--- SEMANTIC SOURCE {index}: {title} ---\n"
        url = result.get("url") or result.get("source_id")
        if url and str(url).startswith("http"):
            formatted += f"URL: {url}\n"
        score = result.get("similarity_score") or result.get("relevance_score")
        if score is not None:
            formatted += f"SIMILARITY: {score}\n"
        snippet = result.get("text_snippet", "")
        formatted += f"\nSUMMARY:\n{snippet}\n\n"
        formatted += "-" * 80 + "\n"
    return formatted


def _run_coro_sync(coro):
    """Run an async coroutine from a synchronous context."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(asyncio.run, coro)
        return future.result()


async def _search_async(
    client: RetrievalMCPClient,
    query: str,
    max_results: int,
) -> tuple[list[dict[str, Any]], list[RetrievedSource]]:
    hits = await client.search(
        query=query,
        search_type="all",
        max_results=max_results,
        tenant_id=get_tenant_id(),
    )
    return hits, sources_from_search_hits(hits)


async def _semantic_search_async(
    client: RetrievalMCPClient,
    query: str,
    max_results: int,
) -> tuple[list[dict[str, Any]], list[RetrievedSource], bool]:
    try:
        hits = await client.semantic_search(
            query=query,
            top_k=max_results,
            tenant_id=get_tenant_id(),
        )
    except Exception:  # noqa: BLE001 - graceful degradation
        return [], [], True
    sources = sources_from_search_hits(hits)
    for src in sources:
        src.source_type = "semantic"
    return hits, sources, False


async def _fetch_async(
    client: RetrievalMCPClient,
    url: str,
) -> tuple[dict[str, Any], RetrievedSource | None]:
    data = await client.fetch(url=url)
    src = source_from_fetch(url, data, config.FETCH_MAX_CHARS)
    return data, src


def run_search(query: str, max_results: int) -> tuple[str, list[RetrievedSource]]:
    """Execute keyword search and return formatted text + source registry updates."""
    client = get_retrieval_client()
    hits, sources = _run_coro_sync(_search_async(client, query, max_results))
    return format_retrieval_results(hits), sources


def run_semantic_search(query: str, max_results: int) -> tuple[str, list[RetrievedSource]]:
    """Execute semantic search and return formatted text + source registry updates."""
    client = get_retrieval_client()
    hits, sources, unavailable = _run_coro_sync(
        _semantic_search_async(client, query, max_results)
    )
    return format_semantic_results(hits, unavailable=unavailable), sources


def run_fetch(url: str) -> tuple[str, RetrievedSource | None]:
    """Fetch a URL and return formatted text + source registry update."""
    client = get_retrieval_client()
    data, src = _run_coro_sync(_fetch_async(client, url))
    return format_fetch_result(data, url), src


def make_mcp_search_provider(
    client: RetrievalMCPClient | None = None,
) -> Callable[[str, int, str], str]:
    """Create a sync search callable backed by the Legal ai retrieval MCP server."""

    def provider(query: str, max_results: int, topic: str) -> str:  # noqa: ARG001
        text, _sources = run_search(query, max_results)
        return text

    return provider


def format_fetch_result(data: dict[str, Any], url: str) -> str:
    """Format a fetched page's full text for the research agent."""
    full_text = data.get("full_text", "")
    title = data.get("title") or url
    if not full_text or "Placeholder" in full_text:
        return f"Could not retrieve content from {url}. The page may be inaccessible or require authentication."
    limit = config.FETCH_MAX_CHARS
    text = full_text[:limit]
    if len(full_text) > limit:
        text += (
            "\n\n[... content truncated — use a more specific fetch or search "
            "for remaining sections ...]"
        )
    return f"--- FULL CONTENT: {title} ---\nURL: {url}\n\n{text}\n" + "-" * 80


def make_mcp_fetch_provider(
    client: RetrievalMCPClient | None = None,
) -> Callable[[str], str]:
    """Create a sync URL fetch callable backed by the Legal ai retrieval MCP server."""

    def provider(url: str) -> str:
        text, _src = run_fetch(url)
        return text

    return provider


def format_memory_results(results: list[dict[str, Any]]) -> str:
    """Format long-term memory matches from the MCP server for the agent."""
    if not results:
        return "No relevant memories found."

    parts = []
    for result in results:
        name = result.get("name", "memory")
        content = result.get("content", "")
        parts.append(f"--- {name} ---\n{content}")
    return "Relevant memories:\n\n" + "\n\n".join(parts)


def make_mcp_memory_search_provider(
    client: RetrievalMCPClient | None = None,
) -> Callable[[str], str]:
    """Create a sync memory-search callable backed by the MCP server."""

    retrieval_client = client or get_retrieval_client()

    def provider(query: str) -> str:
        results = _run_coro_sync(retrieval_client.search_memory(query=query))
        return format_memory_results(results)

    return provider


def make_mcp_memory_save_provider(
    client: RetrievalMCPClient | None = None,
) -> Callable[[str, str, str], str]:
    """Create a sync memory-save callable backed by the MCP server."""

    retrieval_client = client or get_retrieval_client()

    def provider(title: str, content: str, hook: str) -> str:
        data = _run_coro_sync(
            retrieval_client.save_memory(title=title, content=content, hook=hook)
        )
        return data.get("message", f"Memory '{title}' saved.")

    return provider
