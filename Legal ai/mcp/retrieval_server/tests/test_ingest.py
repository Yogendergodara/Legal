"""Tests for internal document ingest."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from mcp.retrieval_server.main import app


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


def test_ingest_internal_endpoint(client: TestClient) -> None:
    with patch(
        "mcp.retrieval_server.ingest_service.embed_text",
        new_callable=AsyncMock,
        return_value=[0.1] * 768,
    ):
        with patch("mcp.retrieval_server.ingest_service.get_session") as mock_session:
            ctx = MagicMock()
            mock_session.return_value.__enter__ = MagicMock(return_value=ctx)
            mock_session.return_value.__exit__ = MagicMock(return_value=False)
            ctx.query.return_value.filter.return_value.first.return_value = None

            response = client.post(
                "/tools/ingest_internal",
                json={
                    "tenant_id": "tenant-x",
                    "title": "NDA Template",
                    "text": "Confidentiality obligations apply for 2 years.",
                },
            )

    assert response.status_code == 200
    data = response.json()
    assert data["tenant_id"] == "tenant-x"
    assert "source_id" in data
    assert data["title"] == "NDA Template"
