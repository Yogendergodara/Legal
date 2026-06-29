"""Tests for CircuitBreaker (Phase 29)."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from review_agent.errors import (
    FatalPipelineError,
    LLMUnavailableError,
    MCPUnreachableError,
    RecoverableError,
)
from review_agent.resilience.circuit_breaker import (
    CircuitBreaker,
    breaker_open_events,
    breaker_open_events_llm,
    breaker_open_events_mcp,
    get_llm_breaker,
    get_mcp_breaker,
    reset_all_breakers,
)


# ---------------------------------------------------------------------------
# Error taxonomy tests
# ---------------------------------------------------------------------------


class TestErrorTaxonomy:
    def test_recoverable_is_exception(self):
        assert issubclass(RecoverableError, Exception)

    def test_fatal_is_exception(self):
        assert issubclass(FatalPipelineError, Exception)

    def test_mcp_unreachable_is_fatal(self):
        assert issubclass(MCPUnreachableError, FatalPipelineError)

    def test_llm_unavailable_is_recoverable(self):
        assert issubclass(LLMUnavailableError, RecoverableError)

    def test_catch_fatal_catches_mcp(self):
        with pytest.raises(FatalPipelineError):
            raise MCPUnreachableError("mcp down")

    def test_catch_recoverable_catches_llm(self):
        with pytest.raises(RecoverableError):
            raise LLMUnavailableError("llm down")


# ---------------------------------------------------------------------------
# CircuitBreaker unit tests
# ---------------------------------------------------------------------------


class TestCircuitBreakerTransitions:
    def test_starts_closed(self):
        cb = CircuitBreaker("test", failure_threshold=3, reset_timeout=10.0)
        assert cb.state == CircuitBreaker.CLOSED
        assert cb.allow() is True

    def test_stays_closed_below_threshold(self):
        cb = CircuitBreaker("test", failure_threshold=3, reset_timeout=10.0)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitBreaker.CLOSED
        assert cb.allow() is True

    def test_opens_at_threshold(self):
        cb = CircuitBreaker("test", failure_threshold=3, reset_timeout=10.0)
        for _ in range(3):
            cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN
        assert cb.allow() is False

    def test_success_resets_count(self):
        cb = CircuitBreaker("test", failure_threshold=3, reset_timeout=10.0)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        # After success, back to CLOSED with count reset
        assert cb.state == CircuitBreaker.CLOSED
        # Need full threshold again to trip
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitBreaker.CLOSED
        assert cb.allow() is True

    def test_half_open_after_timeout(self):
        cb = CircuitBreaker("test", failure_threshold=2, reset_timeout=0.05)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN
        # Wait for reset_timeout
        time.sleep(0.06)
        assert cb.state == CircuitBreaker.HALF_OPEN
        assert cb.allow() is True  # probe allowed

    def test_half_open_success_closes(self):
        cb = CircuitBreaker("test", failure_threshold=2, reset_timeout=0.05)
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.06)
        assert cb.state == CircuitBreaker.HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitBreaker.CLOSED
        assert cb.allow() is True

    def test_half_open_failure_reopens(self):
        cb = CircuitBreaker("test", failure_threshold=2, reset_timeout=0.05)
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.06)
        assert cb.state == CircuitBreaker.HALF_OPEN
        cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN
        assert cb.allow() is False

    def test_reset_clears_state(self):
        cb = CircuitBreaker("test", failure_threshold=2, reset_timeout=10.0)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN
        cb.reset()
        assert cb.state == CircuitBreaker.CLOSED
        assert cb.allow() is True


# ---------------------------------------------------------------------------
# Module-level singleton tests
# ---------------------------------------------------------------------------


class TestSingletons:
    def test_mcp_breaker_is_circuit_breaker(self):
        b = get_mcp_breaker()
        assert isinstance(b, CircuitBreaker)
        assert b.name == "mcp"

    def test_llm_breaker_is_circuit_breaker(self):
        b = get_llm_breaker()
        assert isinstance(b, CircuitBreaker)
        assert b.name == "llm"

    def test_reset_all_breakers(self):
        mcp = get_mcp_breaker()
        llm = get_llm_breaker()
        trips = max(mcp.failure_threshold, llm.failure_threshold)
        for _ in range(trips):
            mcp.record_failure()
            llm.record_failure()
        assert mcp.state == CircuitBreaker.OPEN
        assert llm.state == CircuitBreaker.OPEN
        reset_all_breakers()
        assert mcp.state == CircuitBreaker.CLOSED
        assert llm.state == CircuitBreaker.CLOSED
        assert breaker_open_events() == 0

    def test_open_events_counted_per_breaker(self):
        reset_all_breakers()
        mcp = get_mcp_breaker()
        for _ in range(mcp.failure_threshold):
            mcp.record_failure()
        assert breaker_open_events() == 1
        assert breaker_open_events_mcp() == 1
        assert breaker_open_events_llm() == 0


# ---------------------------------------------------------------------------
# Integration: document_client breaker check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_document_client_fails_fast_on_open_breaker():
    """When MCP breaker is open, _request raises MCPUnreachableError immediately."""
    reset_all_breakers()
    breaker = get_mcp_breaker()
    # Trip the breaker
    for _ in range(breaker.failure_threshold):
        breaker.record_failure()
    assert breaker.state == CircuitBreaker.OPEN

    from review_agent.clients.document_client import DocumentMCPClient

    client = DocumentMCPClient("http://localhost:9999", max_retries=1)
    with pytest.raises(MCPUnreachableError, match="circuit_open:mcp"):
        await client._request("GET", "/health")
    await client.aclose()
    reset_all_breakers()


# ---------------------------------------------------------------------------
# Integration: llm_gateway breaker check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_gateway_fails_fast_on_open_breaker():
    """When LLM breaker is open, invoke_structured raises LLMUnavailableError."""
    reset_all_breakers()
    breaker = get_llm_breaker()
    for _ in range(breaker.failure_threshold):
        breaker.record_failure()
    assert breaker.state == CircuitBreaker.OPEN

    from unittest.mock import MagicMock

    from pydantic import BaseModel

    from review_agent.models.llm_gateway import invoke_structured

    class DummySchema(BaseModel):
        value: str = ""

    mock_model = MagicMock()
    with pytest.raises(LLMUnavailableError, match="circuit_open:llm"):
        await invoke_structured(
            mock_model, DummySchema, system="test", user="test"
        )
    reset_all_breakers()
