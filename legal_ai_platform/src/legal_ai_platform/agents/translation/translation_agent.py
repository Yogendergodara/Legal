"""Stub agent for future translation functionality."""

from legal_ai_platform.agents.base.base_agent import BaseAgent
from legal_ai_platform.models.agent import AgentRequest, AgentResponse


class TranslationAgent(BaseAgent):
    """Legal translation agent (not yet implemented)."""

    agent_type = "translation"

    async def execute(self, request: AgentRequest) -> AgentResponse:
        raise NotImplementedError("TranslationAgent is not yet implemented")
