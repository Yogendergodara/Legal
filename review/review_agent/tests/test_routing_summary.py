"""Tests for routing_summary snapshot persistence (IPC-1.4)."""

from __future__ import annotations

from review_agent.services.routing_limits import reset_routing_limits
from review_agent.services.routing_summary import build_routing_summary


def test_build_routing_summary_uses_snapshots():
    reset_routing_limits()
    summary = build_routing_summary(
        obligation_count=10,
        alias_hit_count=2,
        ipc_count=3,
        compare_count=7,
        planner_calls_snapshot=12,
        catalog_search_calls_snapshot=8,
    )
    assert summary["planner_calls"] == 12
    assert summary["catalog_search_calls"] == 8
