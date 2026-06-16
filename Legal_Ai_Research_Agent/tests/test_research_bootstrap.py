"""Tests for deterministic research bootstrap."""

from unittest.mock import patch

from deep_research_from_scratch.research_bootstrap import bootstrap_legal_research
from deep_research_from_scratch.source_registry import RetrievedSource


def test_expand_search_queries_for_bank_freeze():
    from deep_research_from_scratch.research_bootstrap import _expand_search_queries

    queries = _expand_search_queries(
        "Find cases where courts held that bank accounts cannot be frozen indefinitely"
    )
    assert any("PMLA" in q for q in queries)
    assert any("indefinite" in q for q in queries)
    assert len(queries) >= 5


def test_expand_search_queries_for_crypto():
    from deep_research_from_scratch.research_bootstrap import _expand_search_queries

    queries = _expand_search_queries("cryptocurrency regulation India PMLA")
    # Landmark queries must appear early (not cut off by bootstrap query limit)
    assert queries[0].startswith('site:indiankanoon.org "Internet and Mobile Association"')
    assert any("IAMAI" in q for q in queries[:8])
    assert any("PMLA" in q and ("crypto" in q.lower() or "virtual" in q.lower()) for q in queries[:8])
    assert len(queries) >= 8


def test_bootstrap_disabled_returns_empty():
    with patch("deep_research_from_scratch.research_bootstrap.config") as mock_cfg:
        mock_cfg.ENABLE_RESEARCH_BOOTSTRAP = False
        note, raw, sources = bootstrap_legal_research("bank account freeze", "bank account freeze")
    assert note == ""
    assert raw == ""
    assert sources == []


def test_bootstrap_merges_search_and_fetch():
    search_src = RetrievedSource(
        url="https://indiankanoon.org/doc/123/",
        title="Test Case",
        authority_tier="primary",
        fetched=False,
        excerpt="snippet",
    )
    fetched_src = RetrievedSource(
        url="https://indiankanoon.org/doc/123/",
        title="Test Case",
        authority_tier="primary",
        fetched=True,
        excerpt="Full judgment text about bank freeze.",
    )

    with patch("deep_research_from_scratch.research_bootstrap.config") as mock_cfg:
        mock_cfg.ENABLE_RESEARCH_BOOTSTRAP = True
        mock_cfg.BOOTSTRAP_SEARCH_QUERIES = 3
        mock_cfg.BOOTSTRAP_MAX_FETCHES = 3
        mock_cfg.BOOTSTRAP_RESULTS_PER_QUERY = 5
        mock_cfg.BOOTSTRAP_MIN_TARGET_FETCHES = 3
        with patch(
            "deep_research_from_scratch.research_bootstrap.run_search",
            return_value=("search results", [search_src]),
        ), patch(
            "deep_research_from_scratch.research_bootstrap.run_fetch",
            return_value=("fetch body", fetched_src),
        ):
            note, raw, sources = bootstrap_legal_research(
                "bank account freeze indefinite",
                "Find cases on bank account freeze",
            )

    assert "Bootstrap research" in note
    assert "indiankanoon.org" in raw
    assert any(s.fetched for s in sources)
