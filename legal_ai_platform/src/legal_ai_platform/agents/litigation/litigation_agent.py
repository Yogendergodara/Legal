"""Stub agent for future litigation functionality."""

from legal_ai_platform.agents.base.base_agent import BaseAgent
from legal_ai_platform.models.agent import AgentRequest, AgentResponse


class LitigationAgent(BaseAgent):
    """Litigation analysis agent (not yet implemented)."""

    agent_type = "litigation"

    async def execute(self, request: AgentRequest) -> AgentResponse:
        raise NotImplementedError("LitigationAgent is not yet implemented")
