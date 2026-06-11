"""Stub agent for future compliance functionality."""

from legal_ai_platform.agents.base.base_agent import BaseAgent
from legal_ai_platform.models.agent import AgentRequest, AgentResponse


class ComplianceAgent(BaseAgent):
    """Compliance checking agent (not yet implemented)."""

    agent_type = "compliance"

    async def execute(self, request: AgentRequest) -> AgentResponse:
        raise NotImplementedError("ComplianceAgent is not yet implemented")
