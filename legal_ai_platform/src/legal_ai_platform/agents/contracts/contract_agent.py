"""Stub agent for future contract review functionality."""

from legal_ai_platform.agents.base.base_agent import BaseAgent
from legal_ai_platform.models.agent import AgentRequest, AgentResponse


class ContractAgent(BaseAgent):
    """Contract review agent (not yet implemented)."""

    agent_type = "contract"

    async def execute(self, request: AgentRequest) -> AgentResponse:
        raise NotImplementedError("ContractAgent is not yet implemented")
