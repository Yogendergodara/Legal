"""Research Agent — wraps the existing LangGraph deep research pipeline."""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from deep_research_from_scratch.research_agent_full import deep_researcher_builder
from deep_research_from_scratch.search_tools import (
    clear_retrieval_provider,
    set_retrieval_provider,
)

from legal_ai_platform.agents.base.base_agent import BaseAgent
from legal_ai_platform.agents.research.retrieval_bridge import make_sync_search_provider
from legal_ai_platform.mcp.retrieval_client import RetrievalMCPClient
from legal_ai_platform.models.agent import AgentRequest, AgentResponse
from legal_ai_platform.models.research import ResearchRequest, ResearchResponse
from legal_ai_platform.observability.events import Failure, Latency
from legal_ai_platform.observability.hooks import HookRegistry


class ResearchAgent(BaseAgent):
    """Legal research agent backed by the LangGraph multi-agent pipeline.

    All retrieval is delegated to the injected ``RetrievalMCPClient`` via the
    ``search_tools`` injection seam — this agent never performs direct search.

    The graph is compiled with a checkpointer so a ``thread_id`` continues a
    multi-turn exchange (e.g. answering a clarification question over the API).
    """

    agent_type = "research"

    def __init__(
        self,
        retrieval_client: RetrievalMCPClient,
        hooks: HookRegistry | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> None:
        super().__init__(hooks=hooks)
        self._retrieval_client = retrieval_client
        self._provider_registered = False
        self._timeout_seconds = timeout_seconds if timeout_seconds and timeout_seconds > 0 else None
        # Compile our own instance with a checkpointer for session continuity,
        # without mutating the package's module-level compiled graph.
        self._graph = deep_researcher_builder.compile(checkpointer=MemorySaver())

    def _ensure_retrieval_provider(self) -> None:
        """Register the MCP-backed search provider with search_tools."""
        if not self._provider_registered:
            set_retrieval_provider(make_sync_search_provider(self._retrieval_client))
            os.environ.setdefault("LEGAL_SEARCH_BACKEND", "custom")
            self._provider_registered = True

    async def execute(self, request: AgentRequest) -> AgentResponse:
        """Run the full research pipeline for the given query."""
        self._ensure_retrieval_provider()
        thread_id = request.thread_id or str(uuid.uuid4())
        research_request = ResearchRequest(
            query=request.query,
            context=request.context,
            tenant_id=request.tenant_id,
            max_results=request.max_results,
            thread_id=thread_id,
        )
        run_config = {"configurable": {"thread_id": thread_id}}

        started = time.perf_counter()
        try:
            coro = self._graph.ainvoke(
                {"messages": [HumanMessage(content=research_request.query)]},
                config=run_config,
            )
            if self._timeout_seconds is not None:
                result = await asyncio.wait_for(coro, timeout=self._timeout_seconds)
            else:
                result = await coro
            research_response = self._build_research_response(result)
            latency_ms = (time.perf_counter() - started) * 1000
            self.hooks.emit(
                Latency(operation="research_agent.execute", latency_ms=latency_ms)
            )
            return AgentResponse(
                agent=self.agent_type,
                task_type="research",
                output=research_response.report,
                artifacts={"research": research_response.model_dump()},
                success=True,
                thread_id=thread_id,
                awaiting_input=research_response.awaiting_input,
            )
        except asyncio.TimeoutError:
            latency_ms = (time.perf_counter() - started) * 1000
            self.hooks.emit(
                Failure(
                    operation="research_agent.execute",
                    error=f"timed out after {self._timeout_seconds}s",
                    recoverable=False,
                )
            )
            return AgentResponse(
                agent=self.agent_type,
                task_type="research",
                output="",
                error=f"Research timed out after {self._timeout_seconds}s",
                success=False,
                thread_id=thread_id,
            )
        except Exception as exc:  # noqa: BLE001
            latency_ms = (time.perf_counter() - started) * 1000
            self.hooks.emit(
                Latency(operation="research_agent.execute", latency_ms=latency_ms)
            )
            return AgentResponse(
                agent=self.agent_type,
                task_type="research",
                output="",
                error=str(exc),
                success=False,
                thread_id=thread_id,
            )

    def _build_research_response(self, state: dict[str, Any]) -> ResearchResponse:
        """Map LangGraph final state to ResearchResponse."""
        verification = state.get("verification")
        verification_dict = None
        if verification is not None:
            verification_dict = (
                verification.model_dump()
                if hasattr(verification, "model_dump")
                else dict(verification)
            )

        # When the pipeline short-circuits (e.g. clarify_with_user needs more
        # info), there is no final_report — surface the last assistant message
        # so the API client sees the clarification question instead of "".
        final_report = state.get("final_report")
        awaiting_input = not bool(final_report)
        report = final_report or self._last_ai_text(state)

        return ResearchResponse(
            report=report,
            research_brief=state.get("research_brief"),
            raw_notes=state.get("raw_notes", []),
            verification=verification_dict,
            awaiting_input=awaiting_input,
        )

    @staticmethod
    def _last_ai_text(state: dict[str, Any]) -> str:
        """Return the content of the last assistant message, if any."""
        for message in reversed(state.get("messages", []) or []):
            content = getattr(message, "content", None)
            msg_type = getattr(message, "type", None)
            if msg_type == "ai" and isinstance(content, str) and content.strip():
                return content
        return ""

    def teardown(self) -> None:
        """Clear the retrieval provider on shutdown."""
        clear_retrieval_provider()
        self._provider_registered = False
