"""Stub agent for future drafting functionality."""

from legal_ai_platform.agents.base.base_agent import BaseAgent
from legal_ai_platform.models.agent import AgentRequest, AgentResponse


class DraftingAgent(BaseAgent):
    """Draft generation agent (not yet implemented)."""

    agent_type = "drafting"

    async def execute(self, request: AgentRequest) -> AgentResponse:
        raise NotImplementedError("DraftingAgent is not yet implemented")
