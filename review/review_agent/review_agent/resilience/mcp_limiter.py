"""Process-global concurrency limit for document-mcp HTTP calls."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator

from review_agent.errors import RecoverableError

logger = logging.getLogger(__name__)


@dataclass
class _McpLimiter:
    semaphore: asyncio.Semaphore
    contention_events: int = field(default=0)
    acquire_timeouts: int = field(default=0)
    max_wait_seconds: float = field(default=0.0)


_limiter: _McpLimiter | None = None


def reset_mcp_limiter() -> None:
    """Reset singleton limiter (tests and settings reload)."""
    global _limiter  # noqa: PLW0603
    _limiter = None


def reset_mcp_limiter_stats() -> None:
    """Zero review-scoped contention counters without recreating the semaphore."""
    if _limiter is not None:
        _limiter.contention_events = 0
        _limiter.acquire_timeouts = 0
        _limiter.max_wait_seconds = 0.0


def get_mcp_limiter_stats() -> dict[str, int | float]:
    if _limiter is None:
        return {
            "mcp_semaphore_contention_events": 0,
            "mcp_semaphore_acquire_timeouts": 0,
            "mcp_semaphore_max_wait_seconds": 0.0,
        }
    return {
        "mcp_semaphore_contention_events": _limiter.contention_events,
        "mcp_semaphore_acquire_timeouts": _limiter.acquire_timeouts,
        "mcp_semaphore_max_wait_seconds": round(_limiter.max_wait_seconds, 3),
    }


def _get_limiter() -> _McpLimiter:
    global _limiter  # noqa: PLW0603
    if _limiter is None:
        from review_agent.config import get_settings

        cfg = get_settings()
        _limiter = _McpLimiter(
            semaphore=asyncio.Semaphore(max(1, cfg.mcp_global_concurrency))
        )
    return _limiter


@asynccontextmanager
async def mcp_concurrency_slot(*, method: str, path: str) -> AsyncIterator[None]:
    """Acquire a global MCP slot; warn on long waits; timeout → RecoverableError."""
    from review_agent.config import get_settings

    cfg = get_settings()
    limiter = _get_limiter()
    started = time.monotonic()
    acquire_timeout = cfg.mcp_semaphore_acquire_timeout_seconds

    try:
        if acquire_timeout > 0:
            await asyncio.wait_for(limiter.semaphore.acquire(), timeout=acquire_timeout)
        else:
            await limiter.semaphore.acquire()
    except TimeoutError as exc:
        limiter.acquire_timeouts += 1
        raise RecoverableError(
            f"mcp_semaphore_timeout: no slot within {acquire_timeout}s ({method} {path})"
        ) from exc

    waited = time.monotonic() - started
    if waited > limiter.max_wait_seconds:
        limiter.max_wait_seconds = waited
    if waited >= cfg.mcp_semaphore_acquire_warn_seconds:
        limiter.contention_events += 1
        logger.warning(
            "mcp_semaphore_contention: waited %.1fs for slot (%s %s)",
            waited,
            method,
            path,
        )

    try:
        yield
    finally:
        limiter.semaphore.release()
