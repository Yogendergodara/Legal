"""Tests that WEBSEARCH_BACKEND feature flag switches without touching search_service."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from mcp.retrieval_server.config import Settings
from mcp.retrieval_server.integrations.web_search import WebSearchClient
from mcp.retrieval_server.search_service import SearchService


@pytest.mark.asyncio
async def test_backend_switch_open_websearch() -> None:
    settings = Settings(WEBSEARCH_BACKEND="open-websearch")
    client = httpx.AsyncClient()
    web_client = WebSearchClient(client, settings)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"results": [{"url": "https://a.com", "title": "A"}]}

    with patch.object(client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        results, degraded = await web_client.search("query", 5, "req-sw-1")

    assert len(results) == 1
    assert degraded is False
    mock_post.assert_called_once()


@pytest.mark.asyncio
async def test_backend_switch_legal_index() -> None:
    settings = Settings(WEBSEARCH_BACKEND="legal-index", DATABASE_URL="postgresql://test")
    client = httpx.AsyncClient()
    web_client = WebSearchClient(client, settings)

    with patch(
        "crawler.fts.search_documents",
        new_callable=AsyncMock,
        return_value=[{"url": "https://livelaw.in/x", "title": "Case", "snippet": "s", "score": 0.9, "engine": "legal-index"}],
    ):
        results, degraded = await web_client.search("contract", 5, "req-sw-2")

    assert len(results) == 1
    assert results[0]["engine"] == "legal-index"
    assert degraded is False


@pytest.mark.asyncio
async def test_search_service_unchanged_across_backends() -> None:
    """search_service imports and search_web signature stay stable."""
    settings = Settings(WEBSEARCH_BACKEND="legal-index")
    service = SearchService(httpx.AsyncClient(), settings)

    with patch.object(
        service._web,
        "search",
        new_callable=AsyncMock,
        return_value=([{"url": "https://x.com", "title": "T", "snippet": "s", "score": 0.8}], False),
    ):
        results, degraded = await service.search_web("q", 10, "India", "req-sw-3")

    assert len(results) == 1
    assert results[0].source_type == "web"
    assert degraded is False
