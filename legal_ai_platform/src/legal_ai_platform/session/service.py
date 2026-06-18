"""Platform session service — one transcript + matter for all agents."""

from __future__ import annotations

import uuid
from typing import Any

from legal_ai_platform.models.agent import AgentRequest, AgentResponse
from legal_ai_platform.session.memory_bridge import MemoryBridge
from legal_ai_platform.session.store import SessionStore
from legal_ai_platform.session.models import MatterSnapshot, SessionState, Turn
from legal_ai_platform.session.research_cleanup import delete_legacy_research_session_files


class SessionService:
    """Load/save unified session state; orchestrator calls this every /query turn."""

    def __init__(
        self,
        store: SessionStore,
        memory_bridge: MemoryBridge | None = None,
        *,
        transcript_limit: int = 20,
        platform_owns_session: bool = True,
        delete_legacy_research_files: bool = True,
    ) -> None:
        self._store = store
        self._memory_bridge = memory_bridge
        self._transcript_limit = transcript_limit
        self._platform_owns_session = platform_owns_session
        self._delete_legacy_research_files = delete_legacy_research_files

    @property
    def memory_bridge(self) -> MemoryBridge | None:
        return self._memory_bridge

    def resolve_thread_id(self, thread_id: str | None) -> str:
        return thread_id or str(uuid.uuid4())

    def load_or_create(self, thread_id: str, tenant_id: str) -> SessionState:
        tenant = tenant_id or "default"
        existing = self._store.load(tenant, thread_id)
        if existing is not None:
            return existing
        return SessionState(thread_id=thread_id, tenant_id=tenant)

    def persist(self, state: SessionState) -> None:
        self._store.save(state)

    def append_user_turn(self, state: SessionState, content: str) -> None:
        text = (content or "").strip()
        if not text:
            return
        state.turns.append(Turn(role="user", content=text))

    def append_assistant_turn(
        self,
        state: SessionState,
        *,
        content: str,
        agent: str,
        task_type: str,
    ) -> None:
        text = (content or "").strip()
        if not text:
            return
        state.turns.append(
            Turn(role="assistant", content=text, agent=agent, task_type=task_type)
        )
        state.matter.last_agent = agent
        state.matter.last_task_type = task_type

    def capture_matter_from_request(self, state: SessionState, request: AgentRequest) -> None:
        """Store contract/policy payload on matter when user supplies them."""
        ctx = request.effective_context()
        if request.contract_text or ctx.get("contract_text"):
            state.matter.contract_text = request.contract_text or ctx.get("contract_text")
        if request.contract_title or ctx.get("contract_title"):
            state.matter.contract_title = request.contract_title or ctx.get("contract_title")
        if request.policies is not None or ctx.get("policies"):
            policies = request.policies or ctx.get("policies") or []
            state.matter.policies = [
                p.model_dump() if hasattr(p, "model_dump") else dict(p) for p in policies
            ]
        if request.contract_type or ctx.get("contract_type"):
            state.matter.contract_type = request.contract_type or ctx.get("contract_type")
        if request.policy_type or ctx.get("policy_type"):
            state.matter.policy_type = request.policy_type or ctx.get("policy_type")

    def capture_matter_from_response(
        self,
        state: SessionState,
        response: AgentResponse,
    ) -> None:
        report = response.artifacts.get("report")
        if isinstance(report, dict):
            state.matter.last_review_report = report

    def merge_matter_into_request(self, request: AgentRequest) -> AgentRequest:
        """Fill missing review fields from matter — agents unchanged; orchestrator only."""
        matter: MatterSnapshot | None = None
        session_block = request.context.get("session")
        if isinstance(session_block, dict):
            raw_matter = session_block.get("matter")
            if isinstance(raw_matter, dict):
                matter = MatterSnapshot.model_validate(raw_matter)

        if matter is None:
            return request

        updates: dict[str, Any] = {}
        if not request.contract_text and matter.contract_text:
            updates["contract_text"] = matter.contract_text
        if not request.contract_title and matter.contract_title:
            updates["contract_title"] = matter.contract_title
        if not request.policies and matter.policies:
            updates["policies"] = matter.policies
        if not request.contract_type and matter.contract_type:
            updates["contract_type"] = matter.contract_type
        if not request.policy_type and matter.policy_type:
            updates["policy_type"] = matter.policy_type

        if not updates:
            return request

        return request.model_copy(update=updates)

    def enrich_request(
        self,
        request: AgentRequest,
        state: SessionState,
        *,
        memory_snippets: str = "",
        memory_hits: list[dict[str, Any]] | None = None,
    ) -> AgentRequest:
        """Attach session context; merge matter into top-level review fields."""
        session_context = state.to_context_dict(transcript_limit=self._transcript_limit)
        session_context["memory_snippets"] = memory_snippets
        session_context["memory_hits_count"] = len(memory_hits or [])
        session_context["platform_owns_long_term_memory"] = self._memory_bridge is not None
        session_context["platform_owns_session"] = self._platform_owns_session
        merged_context = {**request.context, "session": session_context}
        enriched = request.model_copy(
            update={"context": merged_context, "thread_id": state.thread_id}
        )
        return self.merge_matter_into_request(enriched)

    async def prefetch_long_term_memory(
        self,
        state: SessionState,
        query: str,
        task_type: str,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Search retrieval-mcp before agent execution (platform-owned recall)."""
        if self._memory_bridge is None:
            return "", []
        return await self._memory_bridge.search(
            query=query,
            tenant_id=state.tenant_id,
            task_type=task_type,
            matter=state.matter,
        )

    async def maybe_persist_long_term_memory(
        self,
        state: SessionState,
        response: AgentResponse,
        task_type: str,
    ) -> dict[str, Any]:
        """Save durable facts after agent turn (review reports only for now)."""
        if self._memory_bridge is None or not response.success:
            return {}

        if task_type != "review":
            return {}

        report = response.artifacts.get("report")
        if not isinstance(report, dict):
            return {}

        contract_title = (
            report.get("contract_title")
            or state.matter.contract_title
            or "Contract"
        )
        result = await self._memory_bridge.save_review_report(
            report,
            tenant_id=state.tenant_id,
            thread_id=state.thread_id,
            contract_title=str(contract_title),
        )
        return result or {}

    def update_summary(self, state: SessionState) -> None:
        """Lightweight rolling summary (no LLM)."""
        if not state.turns:
            state.summary = ""
            return
        parts: list[str] = []
        for turn in state.recent_turns(6):
            prefix = turn.agent or turn.role
            snippet = turn.content[:200].replace("\n", " ")
            parts.append(f"{prefix}: {snippet}")
        state.summary = " | ".join(parts)

    def get_session(self, tenant_id: str, thread_id: str) -> SessionState | None:
        """Load session without creating a new one."""
        return self._store.load(tenant_id or "default", thread_id)

    def delete_session(
        self,
        tenant_id: str,
        thread_id: str,
        *,
        cleanup_legacy_research: bool | None = None,
    ) -> dict[str, Any]:
        """Delete platform session state; optionally remove legacy research JSONL."""
        tenant = tenant_id or "default"
        existed = self._store.exists(tenant, thread_id)
        self._store.delete(tenant, thread_id)
        legacy_deleted: list[str] = []
        should_cleanup = (
            self._delete_legacy_research_files
            if cleanup_legacy_research is None
            else cleanup_legacy_research
        )
        if should_cleanup:
            legacy_deleted = delete_legacy_research_session_files(thread_id)
        return {
            "deleted": existed,
            "thread_id": thread_id,
            "tenant_id": tenant,
            "legacy_research_files_removed": legacy_deleted,
        }
