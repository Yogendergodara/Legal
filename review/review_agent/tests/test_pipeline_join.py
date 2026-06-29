"""Tests for pre_compare_join gate (PG-3)."""

from __future__ import annotations

import pytest

from review_agent.config import ReviewSettings
from review_agent.graph.pipeline_join_nodes import pre_compare_join_node


@pytest.mark.asyncio
async def test_pre_compare_join_ready_when_sections_and_obligations(monkeypatch):
    monkeypatch.setattr(
        "review_agent.graph.pipeline_join_nodes.get_settings",
        lambda: ReviewSettings(
            obligation_routing_enabled=True,
            obligation_routing_tenant_allowlist="e2e-demo",
        ),
    )
    out = await pre_compare_join_node(
        {
            "tenant_id": "e2e-demo",
            "compliance_stats": {
                "sections_retrieved": 3,
                "obligation_compare_ready_count": 2,
            },
            "obligations": [{"obligation_id": "o1"}, {"obligation_id": "o2"}],
            "section_retrieval_by_id": {"s1": {}},
        }
    )
    stats = out["compliance_stats"]
    assert stats["pipeline_join_ready"] is True
    assert stats["pipeline_join_sections"] == 3
    assert stats["pipeline_join_obligations"] == 2


@pytest.mark.asyncio
async def test_pre_compare_join_not_ready_without_obligations(monkeypatch):
    monkeypatch.setattr(
        "review_agent.graph.pipeline_join_nodes.get_settings",
        lambda: ReviewSettings(
            obligation_routing_enabled=True,
            obligation_routing_tenant_allowlist="e2e-demo",
        ),
    )
    out = await pre_compare_join_node(
        {
            "tenant_id": "e2e-demo",
            "compliance_stats": {"sections_retrieved": 2},
            "obligations": [],
            "section_retrieval_by_id": {"s1": {}, "s2": {}},
        }
    )
    assert out["compliance_stats"]["pipeline_join_ready"] is False
    assert any("no obligations" in w for w in out.get("warnings") or [])


@pytest.mark.asyncio
async def test_pre_compare_join_not_ready_without_sections(monkeypatch):
    monkeypatch.setattr(
        "review_agent.graph.pipeline_join_nodes.get_settings",
        lambda: ReviewSettings(obligation_routing_enabled=False),
    )
    out = await pre_compare_join_node(
        {
            "tenant_id": "cisco-beta",
            "compliance_stats": {},
            "obligations": [],
            "section_retrieval_by_id": {},
        }
    )
    assert out["compliance_stats"]["pipeline_join_ready"] is False
    assert any("no section retrieval" in w for w in out.get("warnings") or [])
