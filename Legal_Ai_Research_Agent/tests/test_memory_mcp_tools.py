"""Tests that the memory tools route to the Legal ai MCP server."""

from unittest.mock import patch

from deep_research_from_scratch.memory_mcp_tools import save_memory, search_memory


def test_save_memory_uses_mcp_provider():
    def fake_provider(title, content, hook):
        return f"saved {title} ({hook})"

    with patch("deep_research_from_scratch.memory_mcp_tools._save_provider", fake_provider):
        result = save_memory.invoke({
            "title": "BNS Murder",
            "content": "Section 103 BNS applies on/after 1 July 2024.",
            "hook": "BNS s.103",
        })
        assert result == "saved BNS Murder (BNS s.103)"


def test_search_memory_uses_mcp_provider():
    def fake_provider(query):
        return f"memories for {query}"

    with patch("deep_research_from_scratch.memory_mcp_tools._search_provider", fake_provider):
        result = search_memory.invoke({"query": "limitation period"})
        assert result == "memories for limitation period"
