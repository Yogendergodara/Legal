"""Query orchestrator — classifies, routes, and invokes agents."""

from __future__ import annotations

import time

from legal_ai_platform.models.agent import AgentRequest, AgentResponse
from legal_ai_platform.observability.events import AgentSelected, Failure, Latency, QueryReceived
from legal_ai_platform.observability.hooks import HookRegistry
from legal_ai_platform.orchestration.classifier import TaskClassifier
from legal_ai_platform.orchestration.registry import AgentRegistry


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
