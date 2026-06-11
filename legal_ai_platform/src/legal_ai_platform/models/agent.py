"""Generic agent request/response envelopes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AgentRequest(BaseModel):
    """Generic task envelope sent to any agent via the orchestrator."""

    query: str
    task_type: str | None = Field(
        default=None,
        description="Optional explicit task type; if omitted the classifier decides",
    )
    context: dict[str, Any] = Field(default_factory=dict)
    tenant_id: str | None = None
    max_results: int = Field(default=10, ge=1, le=100)
    thread_id: str | None = Field(
        default=None,
        description="Conversation/session id; reuse to continue a multi-turn exchange",
    )


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
