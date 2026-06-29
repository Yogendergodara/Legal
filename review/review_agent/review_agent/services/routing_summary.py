"""Build routing_summary for compliance_stats (Phase R9)."""

from __future__ import annotations

from typing import Any

from review_agent.services.routing_limits import catalog_search_calls, planner_calls


def build_routing_summary(
    *,
    obligation_count: int,
    alias_hit_count: int,
    ipc_count: int,
    compare_count: int,
    wrong_policy_blocked: int = 0,
    cache_catalog_hit: bool | None = None,
    planner_calls_snapshot: int | None = None,
    catalog_search_calls_snapshot: int | None = None,
) -> dict[str, Any]:
    routed = max(obligation_count, 1)
    return {
        "obligation_count": obligation_count,
        "alias_hit_count": alias_hit_count,
        "alias_hit_rate": round(alias_hit_count / routed, 3),
        "planner_calls_avoided_estimate": alias_hit_count,
        "planner_calls": (
            planner_calls_snapshot if planner_calls_snapshot is not None else planner_calls()
        ),
        "catalog_search_calls": (
            catalog_search_calls_snapshot
            if catalog_search_calls_snapshot is not None
            else catalog_search_calls()
        ),
        "ipc_rate": round(ipc_count / routed, 3),
        "compare_rate": round(compare_count / routed, 3),
        "wrong_policy_blocked": wrong_policy_blocked,
        "cache_catalog_hit": cache_catalog_hit,
    }
