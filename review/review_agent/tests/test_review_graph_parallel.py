"""Tests for PF-1C parallel hybrid graph topology."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from document_core.schemas.compliance import ComplianceFinding, ComplianceStatus, Severity

from review_agent.config import ReviewSettings
from review_agent.graph.review_graph import build_review_graph, resolve_pipeline_wiring
from review_agent.observability.timing import merge_findings


def _finding(finding_id: str, *, label: str = "Cap") -> ComplianceFinding:
    return ComplianceFinding(
        finding_id=finding_id,
        dimension_id=f"s1:{finding_id}",
        dimension_label=label,
        status=ComplianceStatus.NON_COMPLIANT,
        severity=Severity.CRITICAL,
        contract_section_id="s1",
        rationale="Test",
    )


def test_merge_findings_dedupes_by_id():
    left = [_finding("f1"), _finding("f2")]
    right = [_finding("f2", label="Updated")]
    merged = merge_findings(left, right)
    assert len(merged) == 2
    by_id = {f.finding_id: f for f in merged}
    assert by_id["f2"].dimension_label == "Updated"


def test_merge_findings_right_wins_on_conflict():
    left = [_finding("f1", label="old")]
    right = [_finding("f1", label="new")]
    merged = merge_findings(left, right)
    assert len(merged) == 1
    assert merged[0].dimension_label == "new"


def test_merge_findings_empty_branches():
    assert merge_findings(None, None) == []
    assert merge_findings([_finding("f1")], None) == merge_findings(None, [_finding("f1")])


def _edge_set(monkeypatch, tenant_id: str, **settings_kw) -> set[tuple[str, str]]:
    settings = ReviewSettings(**settings_kw)
    monkeypatch.setattr("review_agent.graph.review_graph.get_settings", lambda: settings)
    graph = build_review_graph(AsyncMock(), tenant_id=tenant_id)
    drawable = graph.get_graph()
    return {(e[0], e[1]) for e in drawable.edges}


def test_resolve_parallel_hybrid_for_e2e_demo():
    settings = ReviewSettings(
        obligation_routing_enabled=True,
        obligation_routing_tenant_allowlist="e2e-demo",
        review_pipeline_mode="parallel_hybrid",
    )
    assert resolve_pipeline_wiring("e2e-demo", settings) == "parallel_hybrid"


def test_config_default_pipeline_mode_is_serial():
    assert ReviewSettings.model_fields["review_pipeline_mode"].default == "serial"


def test_resolve_section_only_when_routing_off():
    settings = ReviewSettings(
        obligation_routing_enabled=False,
        review_pipeline_mode="parallel_hybrid",
    )
    assert resolve_pipeline_wiring("cisco-beta", settings) == "section_only"


def test_resolve_serial_hybrid_when_mode_serial():
    settings = ReviewSettings(
        obligation_routing_enabled=True,
        obligation_routing_tenant_allowlist="e2e-demo",
        review_pipeline_mode="serial",
    )
    assert resolve_pipeline_wiring("e2e-demo", settings) == "serial_hybrid"


def test_parallel_topology_chained_retrieval_then_compare_fanout(monkeypatch):
    edges = _edge_set(
        monkeypatch,
        "e2e-demo",
        obligation_routing_enabled=True,
        obligation_routing_tenant_allowlist="e2e-demo",
        review_pipeline_mode="parallel_hybrid",
    )
    assert ("index_policies", "section_policy_retrieval") in edges
    assert ("section_policy_retrieval", "obligation_retrieval") in edges
    assert ("obligation_retrieval", "evidence_sufficiency") in edges
    assert ("evidence_sufficiency", "pre_compare_join") in edges
    assert ("index_policies", "obligation_retrieval") not in edges
    assert ("section_policy_retrieval", "pre_compare_join") not in edges
    assert ("pre_compare_join", "section_compare_llm") in edges
    assert ("pre_compare_join", "obligation_compare") in edges
    assert ("section_compare_llm", "merge_section_findings") in edges
    assert ("obligation_compare", "merge_section_findings") in edges
    assert ("obligation_compare", "section_policy_retrieval") not in edges


def test_serial_topology_preserves_legacy_order(monkeypatch):
    edges = _edge_set(
        monkeypatch,
        "e2e-demo",
        obligation_routing_enabled=True,
        obligation_routing_tenant_allowlist="e2e-demo",
        review_pipeline_mode="serial",
    )
    assert ("obligation_compare", "section_policy_retrieval") in edges
    assert ("index_policies", "obligation_retrieval") in edges
    assert ("pre_compare_join", "section_compare_llm") not in edges


def test_section_only_skips_obligation_nodes(monkeypatch):
    edges = _edge_set(
        monkeypatch,
        "cisco-beta",
        obligation_routing_enabled=False,
        review_pipeline_mode="parallel_hybrid",
    )
    assert ("index_policies", "section_policy_retrieval") in edges
    assert ("index_policies", "obligation_retrieval") not in edges
    assert ("section_policy_retrieval", "section_compare_llm") in edges
