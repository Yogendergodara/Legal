import pytest
from httpx import ASGITransport, AsyncClient

from document_core.schemas.chunk import DocumentKind, IngestRequest
from mcp.document_server.main import app
from review_agent.clients.document_client import DocumentMCPClient
from review_agent.config import get_settings
from review_agent.graph.review_graph import run_review
from tests.fixtures import SAMPLE_CONTRACT, SAMPLE_POLICY


@pytest.mark.asyncio
async def test_document_server_health():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        response = await http.get("/health")
        assert response.status_code == 200
        assert response.json()["service"] == "document-mcp"


@pytest.mark.asyncio
async def test_review_graph_text_e2e():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        client = DocumentMCPClient("http://test", http_client=http)
        result = await run_review(
            client=client,
            tenant_id="demo",
            contract_text=SAMPLE_CONTRACT,
            contract_title="Vendor MSA",
            policy_texts=[{"title": "Vendor Policy", "text": SAMPLE_POLICY}],
            contract_type="msa",
        )
    report = result["report"]
    assert report is not None
    assert report.findings
    assert len(result.get("review_categories") or []) == 2
    assert "Limitation of Liability" in report.summary_markdown
    assert any(
        f.metadata.get("policy_title") == "Vendor Policy"
        for f in report.findings
    )


@pytest.mark.asyncio
async def test_review_graph_contract_only_tenant_auto(monkeypatch):
    """Contract-only path: pre-indexed tenant policies discovered by routing topics."""
    from review_agent.config import get_settings

    monkeypatch.setenv("REVIEW_POLICY_SOURCE", "tenant_auto")
    monkeypatch.setenv("CONTRACT_ROUTING_MODE", "lexical")
    get_settings.cache_clear()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        client = DocumentMCPClient("http://test", http_client=http)
        await client.index_policy(
            IngestRequest(
                tenant_id="demo",
                title="Vendor Policy",
                kind=DocumentKind.POLICY,
                text=SAMPLE_POLICY,
                applies_to_contract_types=["msa"],
            )
        )
        result = await run_review(
            client=client,
            tenant_id="demo",
            contract_text=SAMPLE_CONTRACT,
            contract_title="Vendor MSA",
            contract_type="msa",
        )
    report = result["report"]
    assert report is not None
    assert result.get("discovered_policy_document_ids")
    assert result.get("contract_routing", {}).get("topics")
    assert len(result.get("review_categories") or []) == 2
    assert report.findings
    assert any(
        f.metadata.get("policy_title") == "Vendor Policy"
        for f in report.findings
    )
    assert "Playbook" in report.summary_markdown or "Vendor Policy" in report.summary_markdown
