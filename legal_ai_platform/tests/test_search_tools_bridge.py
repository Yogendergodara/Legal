"""Tests for search_tools retrieval provider injection."""

from deep_research_from_scratch.search_tools import (
    _custom_search,
    clear_retrieval_provider,
    set_retrieval_provider,
)


def test_custom_search_uses_injected_provider():
    def fake_provider(query, max_results, topic):
        return f"results for {query}"

    set_retrieval_provider(fake_provider)
    try:
        assert _custom_search("test query", 3, "general") == "results for test query"
    finally:
        clear_retrieval_provider()


def test_custom_search_raises_without_provider():
    clear_retrieval_provider()
    try:
        import pytest

        with pytest.raises(NotImplementedError):
            _custom_search("test", 3, "general")
    finally:
        clear_retrieval_provider()
