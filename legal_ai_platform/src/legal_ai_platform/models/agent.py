"""Generic agent request/response envelopes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from legal_ai_platform.models.research import ResearchMode


class PolicyInput(BaseModel):
    """Company policy text for contract compliance review."""

    title: str = "Policy"
    text: str = Field(..., min_length=1)
    policy_type: str | None = None
    applies_to_contract_types: list[str] = Field(default_factory=list)


class AgentRequest(BaseModel):
    """Generic task envelope sent to any agent via the orchestrator.

    All clients use ``POST /query`` on the platform gateway. For contract review,
    set ``task_type`` to ``review`` (or rely on classifier) and supply
    ``contract_text`` + ``policies`` (top-level or inside ``context``).
    """

    query: str = ""
    task_type: str | None = Field(
        default=None,
        description="Explicit task type: research, review, drafting, …",
    )
    mode: ResearchMode = Field(
        default=ResearchMode.NORMAL,
        description="Research depth mode: 'normal' (fast, default) or 'deep' (exhaustive memo)",
    )
    context: dict[str, Any] = Field(default_factory=dict)
    tenant_id: str | None = None
    max_results: int = Field(default=10, ge=1, le=100)
    thread_id: str | None = Field(
        default=None,
        description="Conversation/session id; reuse to continue a multi-turn exchange",
    )

    # Review agent fields (optional top-level; may also live in context)
    contract_text: str | None = Field(
        default=None,
        description="Plain contract text for compliance review",
    )
    contract_title: str | None = None
    policies: list[PolicyInput] | None = None
    contract_type: str | None = None
    policy_type: str | None = None
    policy_document_ids: list[str] | None = None
    policy_refs: list[str] | None = None

    @field_validator("policies", mode="before")
    @classmethod
    def coerce_policies(cls, value: Any) -> Any:
        if value is None:
            return None
        if not isinstance(value, list):
            raise TypeError("policies must be a list")
        return value

    def effective_context(self) -> dict[str, Any]:
        """Merge top-level review fields into context for agents."""
        merged = dict(self.context)
        if self.contract_text is not None:
            merged["contract_text"] = self.contract_text
        if self.contract_title is not None:
            merged["contract_title"] = self.contract_title
        if self.policies is not None:
            merged["policies"] = [
                p.model_dump() if isinstance(p, PolicyInput) else p for p in self.policies
            ]
        if self.contract_type is not None:
            merged["contract_type"] = self.contract_type
        if self.policy_type is not None:
            merged["policy_type"] = self.policy_type
        if self.policy_document_ids is not None:
            merged["policy_document_ids"] = self.policy_document_ids
        if self.policy_refs is not None:
            merged["policy_refs"] = self.policy_refs
        return merged


class AgentResponse(BaseModel):
    """Generic response envelope returned by any agent."""

    agent: str
    task_type: str
    output: str = ""
    artifacts: dict[str, Any] = Field(default_factory=dict)
    events: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None
    success: bool = True
    thread_id: str | None = Field(
        default=None,
        description="Session id to pass back on the next request to continue the thread",
    )
    awaiting_input: bool = Field(
        default=False,
        description="True when the agent needs a follow-up reply (e.g. a clarification)",
    )
    research_directions: list[str] = Field(
        default_factory=list,
        description="Pre-research direction options for the user to choose from; non-empty when awaiting_input=True and the agent is presenting research angles",
    )
