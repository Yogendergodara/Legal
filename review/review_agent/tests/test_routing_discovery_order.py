"""Tests for RC-03 discovery-before-match graph wiring."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from review_agent.config import ReviewSettings
from review_agent.graph.review_graph import build_review_graph


def _edge_set(monkeypatch, **settings_kw) -> set[tuple[str, str]]:
    settings = ReviewSettings(
        obligation_routing_enabled=True,
        obligation_routing_tenant_allowlist="e2e-demo",
        review_pipeline_mode="serial",
        **settings_kw,
    )
    monkeypatch.setattr("review_agent.graph.review_graph.get_settings", lambda: settings)
    graph = build_review_graph(AsyncMock(), tenant_id="e2e-demo")
    drawable = graph.get_graph()
    return {(e[0], e[1]) for e in drawable.edges}


def test_discovery_before_match_topology(monkeypatch):
    edges = _edge_set(monkeypatch, routing_discovery_before_match=True)
    assert ("obligation_extract", "contract_routing") in edges
    assert ("contract_routing", "policy_discovery") in edges
    assert ("policy_discovery", "semantic_route") in edges
    assert ("semantic_route", "catalog_match") in edges
    assert ("catalog_match", "index_policies") in edges
    assert ("obligation_extract", "semantic_route") not in edges
    assert ("catalog_match", "contract_routing") not in edges


def test_legacy_routing_topology_when_disabled(monkeypatch):
    edges = _edge_set(monkeypatch, routing_discovery_before_match=False)
    assert ("obligation_extract", "semantic_route") in edges
    assert ("semantic_route", "catalog_match") in edges
    assert ("catalog_match", "contract_routing") in edges
    assert ("contract_routing", "policy_discovery") in edges
    assert ("policy_discovery", "index_policies") in edges
    assert ("policy_discovery", "semantic_route") not in edges
