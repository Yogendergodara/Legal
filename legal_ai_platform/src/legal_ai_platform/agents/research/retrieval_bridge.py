"""Bridge between async RetrievalMCPClient and sync LangGraph web_search tool."""

from __future__ import annotations

import asyncio
import concurrent.futures
from collections.abc import Callable
from typing import TYPE_CHECKING

from legal_ai_platform.models.retrieval import RetrievalResult

if TYPE_CHECKING:
    from legal_ai_platform.mcp.retrieval_client import RetrievalMCPClient


def format_retrieval_results(results: list[RetrievalResult]) -> str:
    """Format retrieval results for the LangGraph research agent."""
    if not results:
        return (
            "No valid search results found. Please try different search queries "
            "or use a different search API."
        )

    formatted = "Search results: \n\n"
    for index, result in enumerate(results, 1):
        formatted += f"\n\n--- SOURCE {index}: {result.title} ---\n"
        if result.url:
            formatted += f"URL: {result.url}\n"
        if result.citation:
            formatted += f"CITATION: {result.citation}\n"
        formatted += f"\nSUMMARY:\n{result.content}\n\n"
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


def make_sync_search_provider(
    client: RetrievalMCPClient,
) -> Callable[[str, int, str], str]:
    """Create a sync search callable for injection into search_tools."""

    def provider(query: str, max_results: int, topic: str) -> str:  # noqa: ARG001
        results = _run_coro_sync(
            client.search(query=query, search_type="all", max_results=max_results)
        )
        return format_retrieval_results(results)

    return provider
