"""Tests for QueryOrchestrator routing."""

import pytest

from legal_ai_platform.agents.base.base_agent import BaseAgent
from legal_ai_platform.models.agent import AgentRequest, AgentResponse
from legal_ai_platform.observability.hooks import HookRegistry
from legal_ai_platform.orchestration.classifier import TaskClassifier
from legal_ai_platform.orchestration.orchestrator import AgentNotFoundError, QueryOrchestrator
from legal_ai_platform.orchestration.registry import AgentRegistry


class _StubResearchAgent(BaseAgent):
    agent_type = "research"

    async def execute(self, request: AgentRequest) -> AgentResponse:
        return AgentResponse(
            agent=self.agent_type,
            task_type="research",
            output=f"Report for: {request.query}",
        )


@pytest.mark.asyncio
async def test_orchestrator_routes_to_registered_agent():
    registry = AgentRegistry()
    registry.register("research", _StubResearchAgent())
    orchestrator = QueryOrchestrator(
        registry=registry,
        classifier=TaskClassifier(),
        hooks=HookRegistry(),
    )
    response = await orchestrator.handle(AgentRequest(query="What is IPC 420?"))
    assert response.success is True
    assert response.agent == "research"
    assert "IPC 420" in response.output


@pytest.mark.asyncio
async def test_orchestrator_raises_when_agent_missing():
    registry = AgentRegistry()
    orchestrator = QueryOrchestrator(registry=registry, classifier=TaskClassifier())
    with pytest.raises(AgentNotFoundError):
        await orchestrator.handle(AgentRequest(query="Review this NDA contract"))


@pytest.mark.asyncio
async def test_orchestrator_respects_explicit_task_type():
    registry = AgentRegistry()
    registry.register("research", _StubResearchAgent())
    orchestrator = QueryOrchestrator(registry=registry, classifier=TaskClassifier())
    response = await orchestrator.handle(
        AgentRequest(query="anything", task_type="research")
    )
    assert response.agent == "research"
