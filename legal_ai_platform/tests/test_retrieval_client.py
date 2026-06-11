"""Tests for RetrievalResult normalization and RetrievalMCPClient."""

from unittest.mock import AsyncMock, patch

import pytest

from legal_ai_platform.mcp.retrieval_client import RetrievalMCPClient
from legal_ai_platform.models.retrieval import RetrievalResult


def test_from_search_hit_maps_fields():
    hit = {
        "source_id": "https://example.com/judgment",
        "source_type": "web",
        "title": "State v. Accused",
        "text_snippet": "The court held that...",
        "url": "https://example.com/judgment",
        "jurisdiction": "India",
        "relevance_score": 0.92,
        "metadata": {"citation": "2020 SCC 1"},
    }
    result = RetrievalResult.from_search_hit(hit)
    assert result.source == "web:https://example.com/judgment"
    assert result.title == "State v. Accused"
    assert result.url == "https://example.com/judgment"
    assert result.content == "The court held that..."
    assert result.citation == "2020 SCC 1"
    assert result.score == pytest.approx(0.92)


def test_from_semantic_hit_uses_similarity_score():
    hit = {
        "source_id": "web:abc",
        "source_type": "web",
        "title": "Gov Notification",
        "text_snippet": "Notification text",
        "similarity_score": 0.85,
        "metadata": {},
    }
    result = RetrievalResult.from_search_hit(hit)
    assert result.score == pytest.approx(0.85)


def test_score_accepts_values_above_one():
    # Semantic similarity scores are not guaranteed to be <= 1.0; must not raise.
    hit = {
        "source_id": "web:abc",
        "source_type": "web",
        "title": "Doc",
        "text_snippet": "text",
        "similarity_score": 1.42,
        "metadata": {},
    }
    result = RetrievalResult.from_search_hit(hit)
    assert result.score == pytest.approx(1.42)


@pytest.mark.asyncio
async def test_search_calls_endpoint():
    client = RetrievalMCPClient(base_url="http://test")
    mock_response = {
        "results": [
            {
                "source_id": "https://example.com/ipc-420",
                "source_type": "web",
                "title": "IPC Section 420",
                "text_snippet": "Cheating",
                "url": "https://example.com/ipc-420",
                "jurisdiction": "India",
                "relevance_score": 0.9,
                "metadata": {},
            }
        ]
    }
    with patch.object(client, "_post", new=AsyncMock(return_value=mock_response)):
        results = await client.search("cheating", search_type="web")
    assert len(results) == 1
    assert results[0].title == "IPC Section 420"


@pytest.mark.asyncio
async def test_search_notifications_delegates_to_web_search():
    client = RetrievalMCPClient(base_url="http://test")
    with patch.object(client, "search", new=AsyncMock(return_value=[])) as mock_search:
        await client.search_notifications("breach of contract")
    mock_search.assert_awaited_once_with(
        "breach of contract",
        search_type="web",
        jurisdiction="India",
        max_results=10,
        filters={"content_type": "notification"},
    )
