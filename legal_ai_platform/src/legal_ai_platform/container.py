"""Dependency injection composition root.

Architecture:
    Client → POST /query (API Gateway) → QueryOrchestrator → AgentRegistry → Agents
                                                                  ↓
                                                    RetrievalMCPClient / DocumentMCPClient
"""

from __future__ import annotations

import os

from pathlib import Path

from legal_ai_platform.agents.research.research_agent import ResearchAgent
from legal_ai_platform.agents.review.review_agent import ReviewAgent
from legal_ai_platform.config import PlatformSettings, get_settings
from legal_ai_platform.mcp.document_client import DocumentMCPClient
from legal_ai_platform.mcp.retrieval_client import RetrievalMCPClient
from legal_ai_platform.observability.hooks import HookRegistry
from legal_ai_platform.orchestration.classifier import TaskClassifier
from legal_ai_platform.orchestration.orchestrator import QueryOrchestrator
from legal_ai_platform.orchestration.registry import AgentRegistry
from legal_ai_platform.session import SessionFileStore, SessionPostgresStore, SessionService
from legal_ai_platform.session.memory_bridge import MemoryBridge
from legal_ai_platform.session.memory_postgres import PostgresMemoryStore
from legal_ai_platform.session.store import SessionStore


class PlatformContainer:
    """Wires all platform dependencies via constructor injection."""

    def __init__(self, settings: PlatformSettings | None = None) -> None:
        # .env is loaded at package import (see legal_ai_platform/__init__.py)
        # so LLM credentials reach model_config before models are constructed.
        self.settings = settings or get_settings()
        os.environ.setdefault("RETRIEVAL_SERVER_URL", self.settings.retrieval_server_url)
        os.environ.setdefault("DOCUMENT_SERVER_URL", self.settings.document_server_url)
        self.hooks = HookRegistry()
        self.retrieval_client = RetrievalMCPClient(
            base_url=self.settings.retrieval_server_url,
            timeout_seconds=self.settings.retrieval_timeout_seconds,
            max_retries=self.settings.retrieval_max_retries,
            hooks=self.hooks,
        )
        self.document_client = DocumentMCPClient(
            base_url=self.settings.document_server_url,
            timeout_seconds=self.settings.document_timeout_seconds,
            max_retries=self.settings.document_max_retries,
            hooks=self.hooks,
        )
        self.registry = AgentRegistry()
        session_store = self._build_session_store()
        memory_bridge = self._build_memory_bridge()
        self.session_service = SessionService(
            session_store,
            memory_bridge=memory_bridge,
            transcript_limit=self.settings.session_transcript_max_turns,
            platform_owns_session=self.settings.platform_owns_session,
            delete_legacy_research_files=self.settings.session_delete_legacy_research_files,
        )
        self._register_agents()
        self.classifier = TaskClassifier()
        self.orchestrator = QueryOrchestrator(
            registry=self.registry,
            classifier=self.classifier,
            hooks=self.hooks,
            session_service=self.session_service,
        )

    def _register_agents(self) -> None:
        """Register all available agents. Future agents are added here."""
        research_agent = ResearchAgent(
            retrieval_client=self.retrieval_client,
            hooks=self.hooks,
            timeout_seconds=self.settings.agent_timeout_seconds,
        )
        self.registry.register("research", research_agent)
        review_agent = ReviewAgent(
            document_client=self.document_client,
            retrieval_client=self.retrieval_client,
            hooks=self.hooks,
        )
        self.registry.register("review", review_agent)

    def _require_database_url(self) -> str:
        database_url = self.settings.database_url
        if not database_url:
            raise ValueError("DATABASE_URL is required for postgres session/memory backends")
        return database_url

    def _build_session_store(self) -> SessionStore:
        if self.settings.session_store_backend == "postgres":
            return SessionPostgresStore(
                self._require_database_url(),
                load_limit=self.settings.session_transcript_load_limit,
            )
        session_dir = Path(self.settings.platform_session_dir)
        return SessionFileStore(session_dir)

    def _build_memory_bridge(self) -> MemoryBridge | None:
        if not self.settings.platform_owns_long_term_memory:
            return None
        max_hits = self.settings.session_memory_max_hits
        if self.settings.memory_store_backend == "postgres":
            return MemoryBridge(
                postgres_store=PostgresMemoryStore(self._require_database_url()),
                max_hits=max_hits,
            )
        return MemoryBridge(self.retrieval_client, max_hits=max_hits)

    async def shutdown(self) -> None:
        """Clean up resources on application shutdown."""
        await self.retrieval_client.close()
        await self.document_client.close()


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
