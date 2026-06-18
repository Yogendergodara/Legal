"""Tests for internal docs client."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from mcp.retrieval_server.config import Settings
from mcp.retrieval_server.integrations.internal_docs import InternalDocsClient


@pytest.mark.asyncio
async def test_internal_docs_search_maps_results() -> None:
    client = InternalDocsClient(Settings())

    with patch(
        "mcp.retrieval_server.integrations.internal_docs.embed_query",
        new_callable=AsyncMock,
        return_value=[0.1] * 768,
    ):
        with patch(
            "mcp.retrieval_server.integrations.internal_docs.hybrid_search_tenant",
            new_callable=AsyncMock,
            return_value=[
                {
                    "source_id": "internal:policy-1",
                    "title": "HR Policy",
                    "text_snippet": "Leave policy details",
                    "score": 0.9,
                }
            ],
        ):
            results = await client.search("leave policy", "tenant-a", 10)

    assert len(results) == 1
    assert results[0].source_type == "internal"
    assert results[0].metadata["tenant_id"] == "tenant-a"
