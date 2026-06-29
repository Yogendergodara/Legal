"""Shared LLM failure classification and review-scoped posture (Phase B)."""

from __future__ import annotations

from contextvars import ContextVar
from enum import Enum
from typing import Any

_QUOTA_MARKERS = (
    "429",
    "rate limit",
    "rate_limited",
    '"code":"1300"',
    "'code':'1300'",
    '"code": "1300"',
)
_NETWORK_MARKERS = (
    "timeout",
    "timed out",
    "connect",
    "connection reset",
    "getaddrinfo",
    "unreachable",
    "dns",
)
_STRUCTURE_MARKERS = (
    "json",
    "parse",
    "schema",
    "validation error",
    "batch too large",
    "expected json",
)
_UNAVAILABLE_MARKERS = ("circuit_open", "llm unavailable")


class FailureClass(str, Enum):
    QUOTA = "quota"
    NETWORK = "network"
    STRUCTURE = "structure"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class ReviewPosture(str, Enum):
    NORMAL = "normal"
    HOT = "hot"
    DEGRADED = "degraded"


_batches_failed: ContextVar[int] = ContextVar("llm_batches_failed", default=0)
_hot_structure_splits: ContextVar[int] = ContextVar("llm_hot_structure_splits", default=0)


def classify_llm_failure(exc_or_reason: BaseException | str) -> FailureClass:
    text = str(exc_or_reason).lower()
    if any(marker in text for marker in _UNAVAILABLE_MARKERS):
        return FailureClass.UNAVAILABLE
    if any(marker in text for marker in _QUOTA_MARKERS):
        return FailureClass.QUOTA
    if any(marker in text for marker in _NETWORK_MARKERS):
        return FailureClass.NETWORK
    if any(marker in text for marker in _STRUCTURE_MARKERS):
        return FailureClass.STRUCTURE
    return FailureClass.UNKNOWN


def is_rate_limited(exc: BaseException) -> bool:
    """True when exc is quota/rate-limit (including wrapped httpx 429)."""
    from review_agent.errors import LLMUnavailableError
    from review_agent.models.llm_gateway import _is_rate_limit_error

    if isinstance(exc, LLMUnavailableError):
        return True
    if _is_rate_limit_error(exc):
        return True
    failure_class = classify_llm_failure(exc)
    return failure_class in (FailureClass.QUOTA, FailureClass.UNAVAILABLE)


def review_posture(stats: dict[str, Any] | None, breaker_state: str) -> ReviewPosture:
    from review_agent.resilience.circuit_breaker import CircuitBreaker

    events = int((stats or {}).get("llm_rate_limit_events", 0))
    if breaker_state == CircuitBreaker.OPEN or events >= 8:
        return ReviewPosture.DEGRADED
    if events >= 3 or breaker_state == CircuitBreaker.HALF_OPEN:
        return ReviewPosture.HOT
    return ReviewPosture.NORMAL


def reset_review_llm_counters() -> None:
    _batches_failed.set(0)
    _hot_structure_splits.set(0)
    from review_agent.config import get_settings
    from review_agent.models.llm_gateway import reset_limiter_rate_limit_events

    if get_settings().llm_review_scope_reset_events:
        reset_limiter_rate_limit_events()


def note_batch_llm_failure() -> None:
    _batches_failed.set(_batches_failed.get() + 1)


def get_batch_failures() -> int:
    return _batches_failed.get()


def get_hot_structure_splits() -> int:
    return _hot_structure_splits.get()


def note_hot_structure_split() -> None:
    _hot_structure_splits.set(_hot_structure_splits.get() + 1)


def get_current_review_posture() -> ReviewPosture:
    from review_agent.models.llm_gateway import get_llm_limiter_stats
    from review_agent.resilience.circuit_breaker import get_llm_breaker

    stats = {"llm_rate_limit_events": get_llm_limiter_stats()["rate_limit_events"]}
    return review_posture(stats, get_llm_breaker().state)


def allow_batch_single_split(
    failure_class: FailureClass,
    posture: ReviewPosture,
    *,
    enabled: bool = True,
) -> bool:
    if not enabled:
        return True
    if failure_class == FailureClass.QUOTA:
        return posture == ReviewPosture.NORMAL
    if failure_class in (FailureClass.STRUCTURE, FailureClass.UNKNOWN):
        return posture != ReviewPosture.DEGRADED
    return posture == ReviewPosture.NORMAL


def should_batch_single_retry(
    exc: BaseException | str,
    *,
    batch_len: int,
    batch_retry_enabled: bool,
    posture_enabled: bool | None = None,
    stage: str = "default",
) -> bool:
    if not batch_retry_enabled or batch_len <= 1:
        return False
    from review_agent.config import get_settings

    cfg = get_settings()
    enabled = (
        posture_enabled
        if posture_enabled is not None
        else cfg.llm_review_posture_enabled
    )
    failure_class = classify_llm_failure(exc)
    posture = get_current_review_posture()
    if not allow_batch_single_split(
        failure_class,
        posture,
        enabled=enabled,
    ):
        return False
    hot_structure = failure_class in (FailureClass.STRUCTURE, FailureClass.UNKNOWN)
    recovery_stage = stage in ("obligation_extract", "section_compare")
    if (
        posture == ReviewPosture.HOT
        and hot_structure
        and cfg.llm_hot_structure_split_max > 0
        and not recovery_stage
        and get_hot_structure_splits() >= cfg.llm_hot_structure_split_max
    ):
        return False
    if posture == ReviewPosture.HOT and hot_structure and not recovery_stage:
        note_hot_structure_split()
    return True


def gateway_max_attempts(
    failure_class: FailureClass,
    posture: ReviewPosture,
    max_retries: int,
    *,
    enabled: bool = True,
) -> int:
    if not enabled or failure_class != FailureClass.QUOTA:
        return max(0, max_retries) + 1
    if posture in (ReviewPosture.HOT, ReviewPosture.DEGRADED):
        return 1
    return max(0, max_retries) + 1


def should_record_breaker_failure(failure_class: FailureClass) -> bool:
    return failure_class not in (FailureClass.QUOTA,)


def enrich_compliance_stats_with_posture(stats: dict[str, Any] | None) -> dict[str, Any]:
    from review_agent.config import get_settings
    from review_agent.models.llm_gateway import get_llm_limiter_stats
    from review_agent.resilience.circuit_breaker import get_llm_breaker

    out = dict(stats or {})
    if not get_settings().llm_review_posture_enabled:
        return out
    out["llm_rate_limit_events"] = get_llm_limiter_stats()["rate_limit_events"]
    out["llm_batches_failed"] = get_batch_failures()
    out["llm_hot_structure_splits_used"] = get_hot_structure_splits()
    out["llm_review_posture"] = review_posture(out, get_llm_breaker().state).value
    return out
