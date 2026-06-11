"""Agent registry for plug-and-play agent discovery."""

from __future__ import annotations

from legal_ai_platform.agents.base.base_agent import BaseAgent


class AgentRegistry:
    """Register, discover, and retrieve agents by task type.

    Agents are registered at startup via dependency injection. The orchestrator
    looks up agents by classified task type — no hardcoded routing.
    """

    def __init__(self) -> None:
        self._agents: dict[str, BaseAgent] = {}

    def register(self, task_type: str, agent: BaseAgent) -> None:
        """Register an agent for a task type."""
        self._agents[task_type] = agent

    def get(self, task_type: str) -> BaseAgent | None:
        """Retrieve an agent by task type, or None if not registered."""
        return self._agents.get(task_type)

    def discover(self) -> dict[str, str]:
        """Return a mapping of task_type -> agent_type for all registered agents."""
        return {task_type: agent.agent_type for task_type, agent in self._agents.items()}

    def list_task_types(self) -> list[str]:
        """Return all registered task types."""
        return list(self._agents.keys())
