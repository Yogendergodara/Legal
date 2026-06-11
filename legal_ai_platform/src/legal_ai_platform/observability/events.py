"""Observability event types."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ObservabilityEvent(BaseModel):
    """Base observability event."""

    event_type: str
    timestamp: datetime = Field(default_factory=_utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class QueryReceived(ObservabilityEvent):
    """Emitted when the orchestrator receives a user query."""

    event_type: Literal["query_received"] = "query_received"
    query: str = ""
    task_type: str | None = None


class AgentSelected(ObservabilityEvent):
    """Emitted when an agent is selected for a task."""

    event_type: Literal["agent_selected"] = "agent_selected"
    task_type: str = ""
    agent_type: str = ""


class ToolCalled(ObservabilityEvent):
    """Emitted when an MCP tool is invoked."""

    event_type: Literal["tool_called"] = "tool_called"
    tool_name: str = ""
    server: str = ""
    latency_ms: float = 0.0
    success: bool = True


class Latency(ObservabilityEvent):
    """Emitted to record operation latency."""

    event_type: Literal["latency"] = "latency"
    operation: str = ""
    latency_ms: float = 0.0


class Failure(ObservabilityEvent):
    """Emitted when an operation fails."""

    event_type: Literal["failure"] = "failure"
    operation: str = ""
    error: str = ""
    recoverable: bool = False
