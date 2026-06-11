"""Tests for Postgres FTS search."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from crawler.fts import _search_documents_sync, search_documents


def test_fts_returns_ranked_rows() -> None:
    row1 = MagicMock()
    row1.url = "https://livelaw.in/high-score"
    row1.title = "High Score Article"
    row1.snippet = "contract breach remedies"
    row1.score = 0.95

    row2 = MagicMock()
    row2.url = "https://livelaw.in/low-score"
    row2.title = "Low Score Article"
    row2.snippet = "contract mention"
    row2.score = 0.3

    mock_session = MagicMock()
    mock_session.execute.return_value.fetchall.return_value = [row1, row2]

    with patch("crawler.fts.get_engine"):
        with patch("crawler.fts.Session") as mock_session_cls:
            mock_session_cls.return_value.__enter__.return_value = mock_session
            results = _search_documents_sync("contract breach", 10, "postgresql://test")

    assert len(results) == 2
    assert results[0]["score"] > results[1]["score"]
    assert results[0]["engine"] == "legal-index"
    assert results[0]["url"] == "https://livelaw.in/high-score"


@pytest.mark.asyncio
async def test_search_documents_async_wrapper() -> None:
    with patch(
        "crawler.fts._search_documents_sync",
        return_value=[{"url": "https://example.com", "title": "T", "snippet": "s", "score": 0.8, "engine": "legal-index"}],
    ):
        results = await search_documents("query", 5, "postgresql://test")

    assert len(results) == 1
    assert results[0]["engine"] == "legal-index"
