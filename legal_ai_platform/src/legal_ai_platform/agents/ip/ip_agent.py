"""Stub agent for future IP law functionality."""

from legal_ai_platform.agents.base.base_agent import BaseAgent
from legal_ai_platform.models.agent import AgentRequest, AgentResponse


class IPAgent(BaseAgent):
    """Intellectual property agent (not yet implemented)."""

    agent_type = "ip"

    async def execute(self, request: AgentRequest) -> AgentResponse:
        raise NotImplementedError("IPAgent is not yet implemented")
