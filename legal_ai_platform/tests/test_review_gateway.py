"""Tests for review routing via the unified orchestrator."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from legal_ai_platform.agents.base.base_agent import BaseAgent
from legal_ai_platform.agents.review.review_agent import ReviewAgent
from legal_ai_platform.container import reset_container
from legal_ai_platform.gateway.app import app as gateway_app
from legal_ai_platform.mcp.document_client import DocumentMCPClient
from legal_ai_platform.models.agent import AgentRequest, AgentResponse
from legal_ai_platform.observability.hooks import HookRegistry
from legal_ai_platform.orchestration.classifier import TaskClassifier
from legal_ai_platform.orchestration.orchestrator import QueryOrchestrator, ReviewPayloadError, AgentNotFoundError
from legal_ai_platform.orchestration.registry import AgentRegistry
from mcp.document_server.main import app as document_app

SAMPLE_CONTRACT = """
12.2 Limitation of Liability
The total liability shall not exceed the fees paid in the twelve (12) months preceding the claim.
"""

SAMPLE_POLICY = """
4. Limitation of Liability
Vendor liability shall not exceed fees paid in the twelve (12) months preceding the claim.
"""


class _StubResearchAgent(BaseAgent):
    agent_type = "research"

    async def execute(self, request: AgentRequest) -> AgentResponse:
        return AgentResponse(
            agent="research",
            task_type="research",
            output="research result",
            success=True,
        )


class _MinimalContainer:
    def __init__(self, orchestrator: QueryOrchestrator, registry: AgentRegistry) -> None:
        self.orchestrator = orchestrator
        self.registry = registry
        self.retrieval_client = None
        self.document_client = None
        self.session_service = orchestrator.session_service

    async def shutdown(self) -> None:
        return None


@pytest.mark.asyncio
async def test_query_endpoint_routes_review():
    doc_transport = ASGITransport(app=document_app)
    async with AsyncClient(transport=doc_transport, base_url="http://doc") as doc_http:
        document_client = DocumentMCPClient("http://doc", http_client=doc_http)
        registry = AgentRegistry()
        registry.register("research", _StubResearchAgent())
        registry.register("review", ReviewAgent(document_client=document_client))
        orchestrator = QueryOrchestrator(registry=registry, hooks=HookRegistry())
        gateway_app.state.container = _MinimalContainer(orchestrator, registry)

        gw_transport = ASGITransport(app=gateway_app)
        async with AsyncClient(transport=gw_transport, base_url="http://gw") as gw_http:
            response = await gw_http.post(
                "/query",
                json={
                    "task_type": "review",
                    "tenant_id": "demo",
                    "contract_title": "MSA",
                    "contract_text": SAMPLE_CONTRACT,
                    "policies": [{"title": "Policy", "text": SAMPLE_POLICY}],
                },
            )

    assert response.status_code == 200
    body = response.json()
    assert body["agent"] == "review"
    assert body["success"] is True
    assert "Compliance Review" in body["output"]
    assert body["artifacts"]["report"]["findings"]


@pytest.mark.asyncio
async def test_review_missing_policies_returns_400():
    registry = AgentRegistry()
    registry.register("research", _StubResearchAgent())
    orchestrator = QueryOrchestrator(registry=registry, hooks=HookRegistry())
    gateway_app.state.container = _MinimalContainer(orchestrator, registry)

    transport = ASGITransport(app=gateway_app)
    async with AsyncClient(transport=transport, base_url="http://gw") as http:
        response = await http.post(
            "/query",
            json={
                "task_type": "review",
                "contract_text": "some contract",
            },
        )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_orchestrator_allows_contract_only_when_tenant_auto(monkeypatch):
    from review_agent.config import get_settings

    monkeypatch.setenv("REVIEW_POLICY_SOURCE", "tenant_auto")
    get_settings.cache_clear()

    orchestrator = QueryOrchestrator(registry=AgentRegistry(), hooks=HookRegistry())
    request = AgentRequest(task_type="review", contract_text="only contract")
    with pytest.raises(AgentNotFoundError):
        await orchestrator.handle(request)


@pytest.mark.asyncio
async def test_orchestrator_validates_review_payload():
    orchestrator = QueryOrchestrator(registry=AgentRegistry(), hooks=HookRegistry())
    request = AgentRequest(task_type="review", contract_text="only contract")
    with pytest.raises(ReviewPayloadError):
        await orchestrator.handle(request)


@pytest.mark.asyncio
async def test_orchestrator_accepts_policy_refs_without_inline_policies():
    orchestrator = QueryOrchestrator(registry=AgentRegistry(), hooks=HookRegistry())
    request = AgentRequest(
        task_type="review",
        contract_text="some contract",
        policy_refs=["catalog-policy-1"],
    )
    with pytest.raises(AgentNotFoundError):
        await orchestrator.handle(request)
