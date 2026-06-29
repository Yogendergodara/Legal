"""Tests for Phase B-RC review-scoped limiter reset."""

from __future__ import annotations

from review_agent.models import llm_gateway
from review_agent.resilience.circuit_breaker import CircuitBreaker
from review_agent.resilience.failure_policy import (
    ReviewPosture,
    reset_review_llm_counters,
    review_posture,
)


def test_reset_review_llm_counters_clears_rate_limit_events(monkeypatch):
    monkeypatch.setenv("LLM_REVIEW_SCOPE_RESET_EVENTS", "true")
    from review_agent.config import get_settings

    llm_gateway.reset_llm_limiter()
    get_settings.cache_clear()
    limiter = llm_gateway._get_limiter()
    limiter.rate_limit_events = 10

    reset_review_llm_counters()

    assert llm_gateway.get_llm_limiter_stats()["rate_limit_events"] == 0
    assert (
        review_posture(
            {"llm_rate_limit_events": limiter.rate_limit_events},
            CircuitBreaker.CLOSED,
        )
        == ReviewPosture.NORMAL
    )


def test_reset_review_llm_counters_can_be_disabled(monkeypatch):
    monkeypatch.setenv("LLM_REVIEW_SCOPE_RESET_EVENTS", "false")
    from review_agent.config import get_settings

    llm_gateway.reset_llm_limiter()
    get_settings.cache_clear()
    limiter = llm_gateway._get_limiter()
    limiter.rate_limit_events = 10

    reset_review_llm_counters()

    assert llm_gateway.get_llm_limiter_stats()["rate_limit_events"] == 10
