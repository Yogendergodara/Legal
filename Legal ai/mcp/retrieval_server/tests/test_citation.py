"""Tests for citation graph service."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mcp.retrieval_server.citation_service import CitationService
from mcp.retrieval_server.config import Settings
from mcp.retrieval_server.models import CitationGraphRequest


def _make_edge(from_id: str, to_id: str) -> MagicMock:
    edge = MagicMock()
    edge.from_source_id = from_id
    edge.to_source_id = to_id
    edge.from_source_type = "web"
    edge.to_source_type = "web"
    edge.citation_type = "cites"
    return edge


@pytest.mark.asyncio
async def test_citation_graph_bfs() -> None:
    service = CitationService(Settings())

    with patch.object(
        service,
        "_get_edges",
        return_value=([_make_edge("https://example.com/a", "https://example.com/b")], []),
    ):
        response = await service.citation_graph(
            CitationGraphRequest(
                source_id="https://example.com/a",
                source_type="web",
                depth=1,
                direction="outgoing",
            ),
            "req-cit-1",
        )

    assert response.stub is False
    assert len(response.nodes) == 2
    assert len(response.edges) == 1
    assert response.edges[0].to_id == "https://example.com/b"
