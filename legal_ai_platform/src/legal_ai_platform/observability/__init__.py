"""Observability package."""

from legal_ai_platform.observability.events import (
    AgentSelected,
    Failure,
    Latency,
    ObservabilityEvent,
    QueryReceived,
    ToolCalled,
)
from legal_ai_platform.observability.hooks import HookRegistry, LoggingHook, ObservabilityHook

__all__ = [
    "AgentSelected",
    "Failure",
    "HookRegistry",
    "Latency",
    "LoggingHook",
    "ObservabilityEvent",
    "ObservabilityHook",
    "QueryReceived",
    "ToolCalled",
]
