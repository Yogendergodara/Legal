"""Base agent abstraction."""

from __future__ import annotations

from abc import ABC, abstractmethod

from legal_ai_platform.models.agent import AgentRequest, AgentResponse
from legal_ai_platform.observability.hooks import HookRegistry


class BaseAgent(ABC):
    """Abstract base for all specialist agents.

    Every agent receives tasks via ``execute``, calls tools through injected
    MCP clients, and returns structured ``AgentResponse`` output.
    """

    agent_type: str = "base"

    def __init__(self, hooks: HookRegistry | None = None) -> None:
        self.hooks = hooks or HookRegistry()

    @abstractmethod
    async def execute(self, request: AgentRequest) -> AgentResponse:
        """Receive a task, process it, and return structured output."""
        ...
