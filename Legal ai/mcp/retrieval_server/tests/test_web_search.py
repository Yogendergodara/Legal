"""Unit tests for WebSearchClient and search_web mapping."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from mcp.retrieval_server.config import Settings
from mcp.retrieval_server.integrations.web_search import WebSearchClient
from mcp.retrieval_server.search_service import SearchService


@pytest.fixture
def settings() -> Settings:
    return Settings(
        WEBSEARCH_BASE_URL="http://open-websearch:3000",
        WEBSEARCH_BACKEND="open-websearch",
    )


@pytest.fixture
def http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient()


class TestWebSearchClient:
    @pytest.mark.asyncio
    async def test_maps_duckduckgo_results(
        self, http_client: httpx.AsyncClient
    ) -> None:
        settings = Settings(WEBSEARCH_BACKEND="duckduckgo")
        client = WebSearchClient(http_client, settings)
        mock_results = [
            {
                "url": "https://livelaw.in/article",
                "title": "Supreme Court Ruling",
                "snippet": "The court held that...",
                "score": 0.85,
                "engine": "duckduckgo",
            }
        ]
        with patch(
            "mcp.retrieval_server.integrations.web_search._duckduckgo_search_sync",
            return_value=mock_results,
        ):
            results, degraded = await client.search("contract law", 10, "req-ddg")

        assert degraded is False
        assert len(results) == 1
        assert results[0]["url"] == "https://livelaw.in/article"

    @pytest.mark.asyncio
    async def test_maps_open_websearch_results(
        self, http_client: httpx.AsyncClient, settings: Settings
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [
                {
                    "url": "https://livelaw.in/article",
                    "title": "Supreme Court Ruling",
                    "snippet": "The court held that...",
                    "score": 0.85,
                    "engine": "duckduckgo",
                }
            ]
        }

        client = WebSearchClient(http_client, settings)
        with patch.object(http_client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            results, degraded = await client.search("contract law", 10, "req-1")

        assert degraded is False
        assert len(results) == 1
        assert results[0]["url"] == "https://livelaw.in/article"

    @pytest.mark.asyncio
    async def test_maps_open_websearch_envelope_response(
        self, http_client: httpx.AsyncClient, settings: Settings
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "ok",
            "data": [
                {
                    "url": "https://livelaw.in/article",
                    "title": "Supreme Court Ruling",
                    "description": "The court held that...",
                    "engine": "duckduckgo",
                }
            ],
            "error": None,
            "hint": None,
        }

        client = WebSearchClient(http_client, settings)
        with patch.object(http_client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            results, degraded = await client.search("contract law", 10, "req-1b")

        assert degraded is False
        assert len(results) == 1
        assert results[0]["url"] == "https://livelaw.in/article"

    @pytest.mark.asyncio
    async def test_timeout_returns_empty_and_degraded(
        self, http_client: httpx.AsyncClient, settings: Settings
    ) -> None:
        client = WebSearchClient(http_client, settings)
        with patch.object(http_client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = httpx.TimeoutException("timeout")
            results, degraded = await client.search("query", 10, "req-2")

        assert results == []
        assert degraded is True


class TestSearchWebMapping:
    @pytest.mark.asyncio
    async def test_search_web_maps_to_search_result_with_backend_metadata(
        self, http_client: httpx.AsyncClient, settings: Settings
    ) -> None:
        service = SearchService(http_client, settings)

        with patch.object(
            service._web,
            "search",
            new_callable=AsyncMock,
            return_value=(
                [
                    {
                        "url": "https://example.com/a",
                        "title": "Article A",
                        "snippet": "Text here",
                        "score": 0.9,
                        "engine": "bing",
                    }
                ],
                False,
            ),
        ):
            results, degraded = await service.search_web(
                "test query", 10, "India", "req-3"
            )

        assert degraded is False
        assert len(results) == 1
        assert results[0].source_type == "web"
        assert results[0].metadata["backend"] == settings.websearch_backend
        assert results[0].metadata["engine"] == "bing"
        assert results[0].url == "https://example.com/a"


class TestNoSecretsLogged:
    @pytest.mark.asyncio
    async def test_web_search_logs_no_api_keys(
        self, http_client: httpx.AsyncClient, settings: Settings, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        caplog.set_level(logging.INFO)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"results": []}

        client = WebSearchClient(http_client, settings)
        with patch.object(http_client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            await client.search("safe query", 5, "req-4")

        log_text = caplog.text + str(mock_post.call_args)
        assert "api_key" not in log_text.lower()
        assert "TAVILY" not in log_text
