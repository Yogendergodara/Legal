"""Query orchestrator — classifies, routes, and invokes agents."""

from __future__ import annotations

import logging
import time

from legal_ai_platform.models.agent import AgentRequest, AgentResponse
from legal_ai_platform.observability.events import AgentSelected, Failure, Latency, QueryReceived
from legal_ai_platform.observability.hooks import HookRegistry
from legal_ai_platform.orchestration.classifier import TaskClassifier
from legal_ai_platform.orchestration.registry import AgentRegistry

logger = logging.getLogger(__name__)


class AgentNotFoundError(Exception):
    """Raised when no agent is registered for the classified task type."""


class QueryOrchestrator:
    """Receive user queries, classify, select agent, invoke, and return response."""

    def __init__(
        self,
        registry: AgentRegistry,
        classifier: TaskClassifier | None = None,
        hooks: HookRegistry | None = None,
    ) -> None:
        self.registry = registry
        self.classifier = classifier or TaskClassifier()
        self.hooks = hooks or HookRegistry()

    async def handle(self, request: AgentRequest) -> AgentResponse:
        """Process a user query end-to-end."""
        started = time.perf_counter()
        task_type = self.classifier.classify(request.query, request.task_type)

        self.hooks.emit(
            QueryReceived(query=request.query, task_type=task_type)
        )

        agent = self.registry.get(task_type)
        if agent is None:
            if request.task_type:
                self._raise_agent_not_found(task_type)
            fallback_type = self.classifier.DEFAULT_TASK_TYPE
            agent = self.registry.get(fallback_type)
            if agent is None or task_type == fallback_type:
                self._raise_agent_not_found(task_type)
            logger.info(
                "No agent for classified task_type=%s; falling back to %s",
                task_type,
                fallback_type,
            )
            task_type = fallback_type

        self.hooks.emit(
            AgentSelected(task_type=task_type, agent_type=agent.agent_type)
        )

        response = await agent.execute(request)
        response.task_type = task_type

        latency_ms = (time.perf_counter() - started) * 1000
        self.hooks.emit(
            Latency(operation="orchestrator.handle", latency_ms=latency_ms)
        )
        return response

    def _raise_agent_not_found(self, task_type: str) -> None:
        self.hooks.emit(
            Failure(
                operation="orchestrator.handle",
                error=f"No agent registered for task_type={task_type}",
                recoverable=False,
            )
        )
        raise AgentNotFoundError(
            f"No agent registered for task_type='{task_type}'. "
            f"Available: {self.registry.list_task_types()}"
        )
