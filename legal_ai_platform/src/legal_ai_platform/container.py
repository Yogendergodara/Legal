"""Dependency injection composition root.

Architecture:
    Client → API Gateway → QueryOrchestrator → AgentRegistry → Agents
                                                          ↓
                                              RetrievalMCPClient → Retrieval Server
"""

from __future__ import annotations

from legal_ai_platform.agents.research.research_agent import ResearchAgent
from legal_ai_platform.config import PlatformSettings, get_settings
from legal_ai_platform.mcp.retrieval_client import RetrievalMCPClient
from legal_ai_platform.observability.hooks import HookRegistry
from legal_ai_platform.orchestration.classifier import TaskClassifier
from legal_ai_platform.orchestration.orchestrator import QueryOrchestrator
from legal_ai_platform.orchestration.registry import AgentRegistry


class PlatformContainer:
    """Wires all platform dependencies via constructor injection."""

    def __init__(self, settings: PlatformSettings | None = None) -> None:
        # .env is loaded at package import (see legal_ai_platform/__init__.py)
        # so LLM credentials reach model_config before models are constructed.
        self.settings = settings or get_settings()
        self.hooks = HookRegistry()
        self.retrieval_client = RetrievalMCPClient(
            base_url=self.settings.retrieval_server_url,
            timeout_seconds=self.settings.retrieval_timeout_seconds,
            max_retries=self.settings.retrieval_max_retries,
            hooks=self.hooks,
        )
        self.registry = AgentRegistry()
        self._register_agents()
        self.classifier = TaskClassifier()
        self.orchestrator = QueryOrchestrator(
            registry=self.registry,
            classifier=self.classifier,
            hooks=self.hooks,
        )

    def _register_agents(self) -> None:
        """Register all available agents. Future agents are added here."""
        research_agent = ResearchAgent(
            retrieval_client=self.retrieval_client,
            hooks=self.hooks,
            timeout_seconds=self.settings.agent_timeout_seconds,
        )
        self.registry.register("research", research_agent)

    async def shutdown(self) -> None:
        """Clean up resources on application shutdown."""
        research = self.registry.get("research")
        if isinstance(research, ResearchAgent):
            research.teardown()
        await self.retrieval_client.close()


_container: PlatformContainer | None = None


def get_container() -> PlatformContainer:
    """Return the singleton platform container."""
    global _container  # noqa: PLW0603
    if _container is None:
        _container = PlatformContainer()
    return _container


def reset_container() -> None:
    """Reset the singleton container (for testing)."""
    global _container  # noqa: PLW0603
    _container = None
