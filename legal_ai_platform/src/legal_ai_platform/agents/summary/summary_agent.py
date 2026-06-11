"""Stub agent for future summary functionality."""

from legal_ai_platform.agents.base.base_agent import BaseAgent
from legal_ai_platform.models.agent import AgentRequest, AgentResponse


class SummaryAgent(BaseAgent):
    """Document summary agent (not yet implemented)."""

    agent_type = "summary"

    async def execute(self, request: AgentRequest) -> AgentResponse:
        raise NotImplementedError("SummaryAgent is not yet implemented")
