"""Tests for Phase 31 observability."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from review_agent.observability.context import bind_review_context, set_current_node
from review_agent.observability.logging import ReviewContextFilter
from review_agent.observability.metrics import configure_metrics, record_mcp_request
from review_agent.observability.timing import merge_compliance_stats, merge_node_timing


def test_context_filter_injects_fields() -> None:
    bind_review_context(tenant_id="t1", thread_id="run-1")
    set_current_node("policy_discovery")
    record = logging.LogRecord(
        name="review_agent.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    assert ReviewContextFilter().filter(record) is True
    assert record.tenant_id == "t1"
    assert record.thread_id == "run-1"
    assert record.node == "policy_discovery"


def test_merge_node_timing_accumulates() -> None:
    state = {"compliance_stats": {"node_timings_ms": {"load_memory": 1.0}}}
    out = merge_node_timing(state, {}, "contract_parser", 2.5)
    assert out["compliance_stats"]["node_timings_ms"] == {
        "load_memory": 1.0,
        "contract_parser": 2.5,
    }


def test_merge_compliance_stats_parallel_branch_timings() -> None:
    section = {
        "compliance_stats": {
            "sections_retrieved": 12,
            "node_timings_ms": {"section_policy_retrieval": 100.0},
        }
    }
    obligation = {
        "compliance_stats": {
            "obligation_retrieved_count": 40,
            "node_timings_ms": {"obligation_retrieval": 200.0},
        }
    }
    merged = merge_compliance_stats(
        section["compliance_stats"],
        obligation["compliance_stats"],
    )
    assert merged["sections_retrieved"] == 12
    assert merged["obligation_retrieved_count"] == 40
    assert merged["node_timings_ms"] == {
        "section_policy_retrieval": 100.0,
        "obligation_retrieval": 200.0,
    }


def test_merge_compliance_stats_parallel_compare_modes() -> None:
    merged = merge_compliance_stats(
        {"compliance_mode": "section_first", "compare_items": 8},
        {"compliance_mode": "obligation_routing", "obligation_compare_count": 3},
    )
    assert merged["compliance_mode"] == "hybrid"
    assert merged["compare_items"] == 8
    assert merged["obligation_compare_count"] == 3


def test_metrics_noop_when_disabled() -> None:
    configure_metrics(False)
    record_mcp_request("/tools/search_policy", "200")


@pytest.mark.asyncio
async def test_run_review_sets_wall_ms(monkeypatch) -> None:
    from review_agent.graph import review_graph

    class _FakeGraph:
        async def ainvoke(self, initial, config=None):
            return dict(initial)

    monkeypatch.setattr(review_graph, "build_review_graph", lambda *a, **k: _FakeGraph())
    monkeypatch.setattr(review_graph, "run_review_preflight", AsyncMock())
    monkeypatch.setattr(
        review_graph,
        "validate_review_inputs",
        lambda **kwargs: (str(kwargs["contract_document_id"]), [], []),
    )

    client = MagicMock()
    result = await review_graph.run_review(
        client=client,
        tenant_id="t1",
        contract_document_id="00000000-0000-4000-8000-000000000001",
        policy_document_ids=["00000000-0000-4000-8000-000000000002"],
    )
    assert "review_wall_ms" in result.get("compliance_stats", {})
