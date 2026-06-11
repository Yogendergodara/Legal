"""Tests for AgentRegistry."""

from legal_ai_platform.agents.base.base_agent import BaseAgent
from legal_ai_platform.models.agent import AgentRequest, AgentResponse
from legal_ai_platform.orchestration.registry import AgentRegistry


class _FakeAgent(BaseAgent):
    agent_type = "fake"

    async def execute(self, request: AgentRequest) -> AgentResponse:
        return AgentResponse(agent=self.agent_type, task_type="fake", output=request.query)


def test_register_and_get():
    registry = AgentRegistry()
    agent = _FakeAgent()
    registry.register("fake", agent)
    assert registry.get("fake") is agent


def test_get_missing_returns_none():
    registry = AgentRegistry()
    assert registry.get("missing") is None


def test_discover():
    registry = AgentRegistry()
    registry.register("research", _FakeAgent())
    assert registry.discover() == {"research": "fake"}


def test_list_task_types():
    registry = AgentRegistry()
    registry.register("research", _FakeAgent())
    registry.register("contract", _FakeAgent())
    assert set(registry.list_task_types()) == {"research", "contract"}
