"""Unit tests for search dedupe and ranking logic."""

from __future__ import annotations

import httpx
import pytest

from mcp.retrieval_server.config import Settings
from mcp.retrieval_server.models import SearchResult
from mcp.retrieval_server.search_service import SearchService


def _make_result(
    source_id: str,
    score: float,
    source_type: str = "web",
    title: str = "Test Result",
) -> SearchResult:
    return SearchResult(
        source_id=source_id,
        source_type=source_type,  # type: ignore[arg-type]
        title=title,
        text_snippet="A snippet of text.",
        url=f"https://example.com/{source_id}",
        jurisdiction="India",
        relevance_score=score,
        metadata={},
    )


@pytest.fixture
def search_service() -> SearchService:
    settings = Settings()
    client = httpx.AsyncClient()
    return SearchService(client, settings)


class TestDedupeAndRank:
    def test_dedupe_collapses_duplicate_source_ids(self, search_service: SearchService) -> None:
        results = [
            _make_result("doc:1", 0.5, title="Lower score duplicate"),
            _make_result("doc:1", 0.9, title="Higher score duplicate"),
            _make_result("doc:2", 0.7, title="Unique result"),
        ]

        final = search_service._dedupe_and_rank(results, max_results=10)

        assert len(final) == 2
        ids = [r.source_id for r in final]
        assert "doc:1" in ids
        assert "doc:2" in ids

        doc1 = next(r for r in final if r.source_id == "doc:1")
        assert doc1.relevance_score == 0.9
        assert doc1.title == "Higher score duplicate"

    def test_ranking_orders_by_relevance_score_desc(self, search_service: SearchService) -> None:
        results = [
            _make_result("doc:1", 0.3),
            _make_result("doc:2", 0.9),
            _make_result("doc:3", 0.6),
        ]

        final = search_service._dedupe_and_rank(results, max_results=10)

        scores = [r.relevance_score for r in final]
        assert scores == sorted(scores, reverse=True)
        assert final[0].source_id == "doc:2"
        assert final[1].source_id == "doc:3"
        assert final[2].source_id == "doc:1"

    def test_max_results_truncates_output(self, search_service: SearchService) -> None:
        results = [_make_result(f"doc:{i}", 1.0 - i * 0.1) for i in range(5)]

        final = search_service._dedupe_and_rank(results, max_results=3)

        assert len(final) == 3
        assert final[0].relevance_score == 1.0

    def test_authority_boost_ranks_indian_kanoon_above_blog(self, search_service: SearchService) -> None:
        ik = _make_result("ik:1", 0.5, title="Indian Kanoon case")
        ik = ik.model_copy(
            update={
                "url": "https://indiankanoon.org/doc/1/",
                "metadata": {"backend": "indiankanoon"},
            }
        )
        blog = _make_result("blog:1", 0.8, title="Blog summary")
        blog = blog.model_copy(update={"url": "https://www.lawsikho.com/murder-law"})

        final = search_service._dedupe_and_rank([blog, ik], max_results=2)

        assert final[0].url.startswith("https://indiankanoon.org")
        assert final[0].metadata.get("authority_tier") == "primary"

    def test_empty_input_returns_empty(self, search_service: SearchService) -> None:
        assert search_service._dedupe_and_rank([], max_results=10) == []
