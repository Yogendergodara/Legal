"""Stub agent for future property law functionality."""

from legal_ai_platform.agents.base.base_agent import BaseAgent
from legal_ai_platform.models.agent import AgentRequest, AgentResponse


class PropertyAgent(BaseAgent):
    """Property law agent (not yet implemented)."""

    agent_type = "property"

    async def execute(self, request: AgentRequest) -> AgentResponse:
        raise NotImplementedError("PropertyAgent is not yet implemented")
