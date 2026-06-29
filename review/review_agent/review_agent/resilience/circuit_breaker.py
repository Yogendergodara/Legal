"""Lightweight circuit breaker for MCP and LLM backends (Phase 29).

Per-process singletons — acceptable for single-worker deployments.
Multi-worker distributed breaker is a Phase 30 concern.
"""

from __future__ import annotations

import logging
import time
from contextvars import ContextVar

logger = logging.getLogger(__name__)

_open_events: ContextVar[int] = ContextVar("breaker_open_events", default=0)
_llm_open_events: ContextVar[int] = ContextVar("breaker_open_events_llm", default=0)
_mcp_open_events: ContextVar[int] = ContextVar("breaker_open_events_mcp", default=0)


class CircuitBreaker:
    """Three-state circuit breaker: CLOSED → OPEN → HALF_OPEN → CLOSED.

    Parameters
    ----------
    name:
        Human-readable label for logging (e.g. ``"mcp"``, ``"llm"``).
    failure_threshold:
        Consecutive failures required to trip the breaker open.
    reset_timeout:
        Seconds after tripping before a single probe request is allowed
        (HALF_OPEN state).
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 5,
        reset_timeout: float = 60.0,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self._state = self.CLOSED
        self._failure_count = 0
        self._opened_at: float = 0.0

    # -- public API ----------------------------------------------------------

    @property
    def state(self) -> str:
        """Current state, updated lazily on timeout expiry."""
        if self._state == self.OPEN:
            if time.monotonic() - self._opened_at >= self.reset_timeout:
                self._state = self.HALF_OPEN
                logger.info("circuit_breaker:%s → HALF_OPEN (timeout expired)", self.name)
        return self._state

    def allow(self) -> bool:
        """Return ``True`` if a request should be attempted."""
        s = self.state  # triggers lazy OPEN→HALF_OPEN transition
        if s == self.CLOSED:
            return True
        if s == self.HALF_OPEN:
            return True  # single probe allowed
        return False  # OPEN

    def record_success(self) -> None:
        """Call after a successful request."""
        if self._state in (self.HALF_OPEN, self.OPEN):
            logger.info("circuit_breaker:%s → CLOSED (success)", self.name)
        self._state = self.CLOSED
        self._failure_count = 0

    def record_failure(self) -> None:
        """Call after a failed request."""
        self._failure_count += 1
        if self._state == self.HALF_OPEN:
            # probe failed — re-open immediately
            self._state = self.OPEN
            self._opened_at = time.monotonic()
            self._record_open()
            logger.warning(
                "circuit_breaker:%s → OPEN (half-open probe failed, count=%s)",
                self.name,
                self._failure_count,
            )
        elif self._failure_count >= self.failure_threshold:
            if self._state != self.OPEN:
                self._record_open()
            self._state = self.OPEN
            self._opened_at = time.monotonic()
            logger.warning(
                "circuit_breaker:%s → OPEN (threshold=%s reached)",
                self.name,
                self.failure_threshold,
            )

    def _record_open(self) -> None:
        _open_events.set(_open_events.get() + 1)
        if self.name == "llm":
            _llm_open_events.set(_llm_open_events.get() + 1)
        elif self.name == "mcp":
            _mcp_open_events.set(_mcp_open_events.get() + 1)

    def reset(self) -> None:
        """Force-reset to CLOSED (tests / settings reload)."""
        self._state = self.CLOSED
        self._failure_count = 0
        self._opened_at = 0.0


# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------

_mcp_breaker = CircuitBreaker("mcp", failure_threshold=5, reset_timeout=60.0)
_llm_breaker = CircuitBreaker("llm", failure_threshold=15, reset_timeout=60.0)


def reset_breaker_open_events() -> None:
    _open_events.set(0)
    _llm_open_events.set(0)
    _mcp_open_events.set(0)


def breaker_open_events() -> int:
    return _open_events.get()


def breaker_open_events_llm() -> int:
    return _llm_open_events.get()


def breaker_open_events_mcp() -> int:
    return _mcp_open_events.get()


def get_mcp_breaker() -> CircuitBreaker:
    return _mcp_breaker


def get_llm_breaker() -> CircuitBreaker:
    return _llm_breaker


def reset_all_breakers() -> None:
    """Reset both breakers (tests / settings reload)."""
    _mcp_breaker.reset()
    _llm_breaker.reset()
    reset_breaker_open_events()
