"""Tests for Phase B failure policy and review posture."""

from __future__ import annotations

import pytest

from review_agent.resilience.circuit_breaker import CircuitBreaker
from review_agent.resilience.failure_policy import (
    FailureClass,
    ReviewPosture,
    allow_batch_single_split,
    classify_llm_failure,
    gateway_max_attempts,
    review_posture,
    should_batch_single_retry,
    should_record_breaker_failure,
)


def test_classify_quota():
    assert classify_llm_failure("HTTP 429 rate limit exceeded") == FailureClass.QUOTA
    assert classify_llm_failure('"code":"1300"') == FailureClass.QUOTA


def test_classify_structure():
    assert classify_llm_failure("JSON parse error") == FailureClass.STRUCTURE
    assert classify_llm_failure("validation error for batch") == FailureClass.STRUCTURE


def test_classify_network():
    assert classify_llm_failure("getaddrinfo failed") == FailureClass.NETWORK
    assert classify_llm_failure("connection reset by peer") == FailureClass.NETWORK


def test_classify_unavailable():
    assert classify_llm_failure("circuit_open:llm") == FailureClass.UNAVAILABLE


def test_posture_normal():
    assert review_posture({"llm_rate_limit_events": 0}, CircuitBreaker.CLOSED) == ReviewPosture.NORMAL


def test_posture_hot_on_events():
    assert review_posture({"llm_rate_limit_events": 3}, CircuitBreaker.CLOSED) == ReviewPosture.HOT


def test_posture_hot_on_half_open():
    assert review_posture({"llm_rate_limit_events": 0}, CircuitBreaker.HALF_OPEN) == ReviewPosture.HOT


def test_posture_degraded_on_breaker():
    assert review_posture({"llm_rate_limit_events": 0}, CircuitBreaker.OPEN) == ReviewPosture.DEGRADED


def test_posture_degraded_on_many_events():
    assert review_posture({"llm_rate_limit_events": 8}, CircuitBreaker.CLOSED) == ReviewPosture.DEGRADED


def test_allow_split_structure_normal():
    assert allow_batch_single_split(FailureClass.STRUCTURE, ReviewPosture.NORMAL) is True


def test_allow_split_quota_hot():
    assert allow_batch_single_split(FailureClass.QUOTA, ReviewPosture.HOT) is False


def test_allow_split_disabled_legacy():
    assert allow_batch_single_split(FailureClass.QUOTA, ReviewPosture.HOT, enabled=False) is True


def test_should_batch_single_retry_429_hot(monkeypatch):
    from review_agent.models import llm_gateway

    monkeypatch.setenv("LLM_REVIEW_POSTURE_ENABLED", "true")
    from review_agent.config import get_settings

    llm_gateway.reset_llm_limiter()
    get_settings.cache_clear()
    limiter = llm_gateway._get_limiter()
    limiter.rate_limit_events = 3

    assert (
        should_batch_single_retry(
            RuntimeError("429 Too Many Requests"),
            batch_len=4,
            batch_retry_enabled=True,
        )
        is False
    )


def test_allow_split_structure_hot():
    assert allow_batch_single_split(FailureClass.STRUCTURE, ReviewPosture.HOT) is True


def test_allow_split_structure_degraded():
    assert allow_batch_single_split(FailureClass.STRUCTURE, ReviewPosture.DEGRADED) is False


def test_should_batch_single_retry_structure_hot(monkeypatch):
    from review_agent.models import llm_gateway

    monkeypatch.setenv("LLM_REVIEW_POSTURE_ENABLED", "true")
    from review_agent.config import get_settings

    llm_gateway.reset_llm_limiter()
    get_settings.cache_clear()
    limiter = llm_gateway._get_limiter()
    limiter.rate_limit_events = 3

    assert (
        should_batch_single_retry(
            RuntimeError("JSON parse error in batch"),
            batch_len=4,
            batch_retry_enabled=True,
        )
        is True
    )


def test_hot_structure_split_cap(monkeypatch):
    from review_agent.models import llm_gateway
    from review_agent.resilience.failure_policy import (
        get_hot_structure_splits,
        reset_review_llm_counters,
    )

    monkeypatch.setenv("LLM_REVIEW_POSTURE_ENABLED", "true")
    monkeypatch.setenv("LLM_HOT_STRUCTURE_SPLIT_MAX", "2")
    from review_agent.config import get_settings

    llm_gateway.reset_llm_limiter()
    get_settings.cache_clear()
    reset_review_llm_counters()
    limiter = llm_gateway._get_limiter()
    limiter.rate_limit_events = 3

    exc = RuntimeError("validation error for batch")
    assert should_batch_single_retry(exc, batch_len=4, batch_retry_enabled=True) is True
    assert get_hot_structure_splits() == 1
    assert should_batch_single_retry(exc, batch_len=4, batch_retry_enabled=True) is True
    assert get_hot_structure_splits() == 2
    assert should_batch_single_retry(exc, batch_len=4, batch_retry_enabled=True) is False


def test_should_batch_single_retry_structure_normal():
    assert (
        should_batch_single_retry(
            RuntimeError("batch schema validation error"),
            batch_len=4,
            batch_retry_enabled=True,
            posture_enabled=True,
        )
        is True
    )


def test_gateway_max_attempts_hot_quota():
    assert gateway_max_attempts(FailureClass.QUOTA, ReviewPosture.HOT, 3) == 1


def test_gateway_max_attempts_normal_quota():
    assert gateway_max_attempts(FailureClass.QUOTA, ReviewPosture.NORMAL, 3) == 4


def test_gateway_max_attempts_disabled():
    assert gateway_max_attempts(FailureClass.QUOTA, ReviewPosture.HOT, 3, enabled=False) == 4


def test_breaker_skips_quota_failures():
    assert should_record_breaker_failure(FailureClass.QUOTA) is False
    assert should_record_breaker_failure(FailureClass.STRUCTURE) is True
