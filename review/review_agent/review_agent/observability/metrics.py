"""Prometheus metrics — no-op unless enabled (Phase 31)."""

from __future__ import annotations

_ENABLED = False
_COUNTERS: dict[str, object] = {}
_HISTOGRAMS: dict[str, object] = {}


def configure_metrics(enabled: bool) -> None:
    global _ENABLED
    _ENABLED = enabled


def _metric(name: str, factory):
    if name not in _COUNTERS and name not in _HISTOGRAMS:
        try:
            return factory()
        except Exception:
            return None
    return _COUNTERS.get(name) or _HISTOGRAMS.get(name)


def record_review_duration(seconds: float) -> None:
    if not _ENABLED:
        return
    try:
        from prometheus_client import Histogram
    except ImportError:
        return
    hist = _HISTOGRAMS.get("review_duration_seconds")
    if hist is None:
        hist = Histogram("review_duration_seconds", "Full review wall time")
        _HISTOGRAMS["review_duration_seconds"] = hist
    hist.observe(seconds)  # type: ignore[union-attr]


def record_node_duration(node: str, seconds: float) -> None:
    if not _ENABLED:
        return
    try:
        from prometheus_client import Histogram
    except ImportError:
        return
    hist = _HISTOGRAMS.get("review_node_duration_seconds")
    if hist is None:
        hist = Histogram(
            "review_node_duration_seconds",
            "Per-node wall time",
            ["node"],
        )
        _HISTOGRAMS["review_node_duration_seconds"] = hist
    hist.labels(node=node).observe(seconds)  # type: ignore[union-attr]


def record_mcp_request(path: str, status: str) -> None:
    if not _ENABLED:
        return
    try:
        from prometheus_client import Counter
    except ImportError:
        return
    counter = _COUNTERS.get("review_mcp_requests_total")
    if counter is None:
        counter = Counter(
            "review_mcp_requests_total",
            "Document MCP HTTP requests",
            ["path", "status"],
        )
        _COUNTERS["review_mcp_requests_total"] = counter
    counter.labels(path=path, status=status).inc()  # type: ignore[union-attr]


def _routing_counter(name: str, description: str):
    if not _ENABLED:
        return None
    try:
        from prometheus_client import Counter
    except ImportError:
        return None
    counter = _COUNTERS.get(name)
    if counter is None:
        counter = Counter(name, description)
        _COUNTERS[name] = counter
    return counter


def record_routing_alias_hit() -> None:
    counter = _routing_counter("obligation_routing_alias_hit_total", "Alias fast-path routing hits")
    if counter is not None:
        counter.inc()  # type: ignore[union-attr]


def record_routing_planner_call() -> None:
    counter = _routing_counter("obligation_routing_planner_calls_total", "Semantic planner LLM batches")
    if counter is not None:
        counter.inc()  # type: ignore[union-attr]


def record_routing_ipc() -> None:
    counter = _routing_counter("obligation_routing_ipc_total", "Obligation evidence IPC decisions")
    if counter is not None:
        counter.inc()  # type: ignore[union-attr]


def record_routing_compare() -> None:
    counter = _routing_counter("obligation_routing_compare_total", "Obligation evidence compare decisions")
    if counter is not None:
        counter.inc()  # type: ignore[union-attr]


def record_wrong_policy_blocked() -> None:
    counter = _routing_counter("obligation_wrong_policy_blocked_total", "Wrong-policy compare blocks")
    if counter is not None:
        counter.inc()  # type: ignore[union-attr]


def record_llm_call(operation: str, status: str) -> None:
    if not _ENABLED:
        return
    try:
        from prometheus_client import Counter
    except ImportError:
        return
    counter = _COUNTERS.get("review_llm_calls_total")
    if counter is None:
        counter = Counter(
            "review_llm_calls_total",
            "LLM structured calls",
            ["operation", "status"],
        )
        _COUNTERS["review_llm_calls_total"] = counter
    counter.labels(operation=operation, status=status).inc()  # type: ignore[union-attr]
