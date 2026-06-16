"""Tests for search_tools Legal ai MCP routing."""

from unittest.mock import patch

from deep_research_from_scratch.search_tools import _run_search, web_search


def test_run_search_uses_mcp_provider():
    with patch(
        "deep_research_from_scratch.search_tools.run_search",
        return_value=("results for Section 420 IPC", []),
    ):
        result = _run_search("Section 420 IPC", 5, "general")
        assert result == "results for Section 420 IPC"


def test_web_search_tool_invokes_mcp_provider():
    with patch(
        "deep_research_from_scratch.search_tools.run_search",
        return_value=("MCP hit: BNS theft provisions", []),
    ):
        result = web_search.invoke({"query": "BNS theft provisions"})
        assert result == "MCP hit: BNS theft provisions"
