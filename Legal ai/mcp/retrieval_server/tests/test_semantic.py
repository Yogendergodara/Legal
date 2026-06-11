"""Tests for semantic search service."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from mcp.retrieval_server.config import Settings
from mcp.retrieval_server.models import SemanticSearchRequest
from mcp.retrieval_server.semantic_service import SemanticSearchService


@pytest.mark.asyncio
async def test_semantic_search_returns_results() -> None:
    settings = Settings()
    service = SemanticSearchService(settings)

    with patch(
        "mcp.retrieval_server.semantic_service.embed_text",
        new_callable=AsyncMock,
        return_value=[0.1] * 384,
    ):
        with patch(
            "mcp.retrieval_server.semantic_service.semantic_search_web",
            new_callable=AsyncMock,
            return_value=[
                {
                    "source_id": "https://example.com/a",
                    "source_type": "web",
                    "title": "Article",
                    "text_snippet": "snippet",
                    "similarity_score": 0.85,
                }
            ],
        ):
            response = await service.semantic_search(
                SemanticSearchRequest(query="contract", threshold=0.5),
                "req-sem-1",
            )

    assert response.stub is False
    assert response.total_results == 1
    assert response.results[0].similarity_score == 0.85


@pytest.mark.asyncio
async def test_semantic_search_failure_returns_stub() -> None:
    service = SemanticSearchService(Settings())

    with patch(
        "mcp.retrieval_server.semantic_service.embed_text",
        new_callable=AsyncMock,
        side_effect=RuntimeError("embed failed"),
    ):
        response = await service.semantic_search(
            SemanticSearchRequest(query="test"), "req-sem-2"
        )

    assert response.stub is True
    assert response.total_results == 0
