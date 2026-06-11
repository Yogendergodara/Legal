"""Vendor-agnostic observability hook registry."""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from legal_ai_platform.observability.events import ObservabilityEvent

logger = logging.getLogger(__name__)


@runtime_checkable
class ObservabilityHook(Protocol):
    """Protocol for pluggable observability sinks."""

    def handle(self, event: ObservabilityEvent) -> None:
        """Process an observability event."""
        ...


class LoggingHook:
    """Default hook that logs events to the standard logger."""

    def handle(self, event: ObservabilityEvent) -> None:
        logger.info(
            "observability event=%s metadata=%s",
            event.event_type,
            event.model_dump(exclude={"timestamp"}),
        )


class HookRegistry:
    """Fan-out registry for observability events.

    Register vendor-specific hooks (Langfuse, OpenTelemetry, Prometheus)
    without hardcoding any vendor in core platform code.
    """

    def __init__(self) -> None:
        self._hooks: list[ObservabilityHook] = [LoggingHook()]

    def register(self, hook: ObservabilityHook) -> None:
        """Register an additional observability hook."""
        self._hooks.append(hook)

    def emit(self, event: ObservabilityEvent) -> None:
        """Emit an event to all registered hooks."""
        for hook in self._hooks:
            try:
                hook.handle(event)
            except Exception:  # noqa: BLE001 - hooks must not break the pipeline
                logger.exception("observability hook failed for event=%s", event.event_type)
