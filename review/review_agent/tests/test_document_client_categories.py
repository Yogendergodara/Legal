"""P0-1 regression: document client sends categories via SearchRequest.metadata."""

from __future__ import annotations

from uuid import uuid4

import pytest

from document_core.schemas.chunk import DocumentKind, SearchRequest
from review_agent.clients.document_client import DocumentMCPClient


@pytest.mark.asyncio
async def test_search_policy_by_categories_payload_shape() -> None:
    captured: list[dict] = []

    class _FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"results": []}

    class _FakeHttp:
        async def request(self, method: str, url: str, json: dict | None = None, **kwargs) -> _FakeResponse:
            captured.append({"url": url, "json": json})
            return _FakeResponse()

    client = DocumentMCPClient("http://localhost:8003", http_client=_FakeHttp())
    request = SearchRequest(
        tenant_id="e2e-demo",
        query="indemnification gross negligence",
        kind=DocumentKind.POLICY,
        contract_type="nda",
        top_k=5,
    )
    await client.search_policy_by_categories(request, categories=["indemnification", "liability"])

    assert len(captured) == 1
    assert captured[0]["url"].endswith("/tools/search_policy_by_categories")
    payload = captured[0]["json"]
    assert payload["metadata"]["categories"] == ["indemnification", "liability"]
    assert payload["tenant_id"] == "e2e-demo"
    assert payload["query"] == "indemnification gross negligence"


@pytest.mark.asyncio
async def test_search_policy_by_categories_merges_existing_metadata() -> None:
    captured: list[dict] = []

    class _FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"results": []}

    class _FakeHttp:
        async def request(self, method: str, url: str, json: dict | None = None, **kwargs) -> _FakeResponse:
            captured.append(json)
            return _FakeResponse()

    client = DocumentMCPClient("http://localhost:8003", http_client=_FakeHttp())
    request = SearchRequest(
        tenant_id="t",
        query="q",
        metadata={"source": "test-harness", "categories": ["old"]},
    )
    await client.search_policy_by_categories(request, categories=["liability"])

    meta = captured[0]["metadata"]
    assert meta["source"] == "test-harness"
    assert meta["categories"] == ["liability"]
