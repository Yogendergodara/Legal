"""Tests for legal authority site-restricted search."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from mcp.retrieval_server.integrations.legal_authority_search import LegalAuthoritySearchClient


@pytest.mark.asyncio
async def test_legal_authority_search_merges_domain_hits() -> None:
    client = LegalAuthoritySearchClient(timeout=5.0)

    def fake_ddg(query: str, max_results: int, timeout: float = 8.0) -> list[dict]:
        if "indiankanoon.org" in query:
            return [
                {
                    "url": "https://indiankanoon.org/doc/123/",
                    "title": "Test Case",
                    "body": "Murder ingredients",
                }
            ]
        if "indiacode.nic.in" in query:
            return [
                {
                    "url": "https://www.indiacode.nic.in/show-data?actid=123",
                    "title": "BNS Section 103",
                    "body": "Punishment for murder",
                }
            ]
        return []

    with patch(
        "mcp.retrieval_server.integrations.legal_authority_search._duckduckgo_search_sync",
        side_effect=fake_ddg,
    ):
        results, degraded = await client.search("murder punishment", max_results=5)

    assert degraded is False
    assert len(results) >= 2
    urls = {item["url"] for item in results}
    assert any("indiankanoon.org" in url for url in urls)
    assert any("indiacode.nic.in" in url for url in urls)
