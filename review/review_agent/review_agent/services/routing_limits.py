"""Per-review routing counters for cost controls (Phase R9)."""

from __future__ import annotations

from contextvars import ContextVar

_planner_calls: ContextVar[int] = ContextVar("routing_planner_calls", default=0)
_catalog_search_calls: ContextVar[int] = ContextVar("routing_catalog_search_calls", default=0)


def reset_routing_limits() -> None:
    _planner_calls.set(0)
    _catalog_search_calls.set(0)


def planner_calls() -> int:
    return _planner_calls.get()


def catalog_search_calls() -> int:
    return _catalog_search_calls.get()


def increment_planner_calls() -> int:
    value = _planner_calls.get() + 1
    _planner_calls.set(value)
    return value


def increment_catalog_search_calls() -> int:
    value = _catalog_search_calls.get() + 1
    _catalog_search_calls.set(value)
    return value
