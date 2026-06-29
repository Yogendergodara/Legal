"""Parallel hybrid graph invoke smoke (PG-6)."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk
from document_core.schemas.compliance import ComplianceStatus, Severity

from review_agent.config import ReviewSettings
from review_agent.graph.review_graph import build_review_graph, resolve_pipeline_wiring


def _noop_async(*_args, **_kwargs):
    async def _inner(_state, *_a, **_k):
        return {}

    return _inner


def _section(section_id: str, text: str) -> dict:
    return IndexedChunk(
        chunk_id="c1",
        document_id=uuid4(),
        tenant_id="e2e-demo",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id=section_id,
        section_path=section_id,
        title="Liability",
        text=text,
    ).model_dump(mode="json")


@pytest.mark.asyncio
async def test_parallel_hybrid_invoke_merges_compare_branches(monkeypatch):
    """Parallel compare branches complete without InvalidUpdateError."""
    settings = ReviewSettings(
        obligation_routing_enabled=True,
        obligation_routing_tenant_allowlist="e2e-demo",
        review_pipeline_mode="parallel_hybrid",
        compare_branch_fail_open=True,
        guard_pass_enabled=False,
        final_gap_verify_enabled=False,
        grounding_rerun_coverage=False,
        enforce_section_coverage=False,
    )
    monkeypatch.setattr("review_agent.graph.review_graph.get_settings", lambda: settings)

    from review_agent.graph import review_graph as rg_mod

    async def stub_upstream(_state, *_args, **_kwargs):
        return {}

    async def stub_section_retrieval(state, *_args, **_kwargs):
        return {
            "section_retrieval_by_id": {
                "s1": {
                    "section_id": "s1",
                    "categories": ["liability"],
                    "policy_hits": [],
                    "retrieval_meta": {"substantive": True},
                }
            },
            "section_review_sections": [
                _section("s1", "The vendor liability shall not exceed fees paid."),
            ],
            "compliance_stats": {
                **dict(state.get("compliance_stats") or {}),
                "sections_retrieved": 1,
            },
        }

    async def stub_obligation_retrieval(state, *_args, **_kwargs):
        return {
            "obligation_retrieval_by_id": {},
            "compliance_stats": dict(state.get("compliance_stats") or {}),
        }

    async def stub_evidence(state, *_args, **_kwargs):
        return {
            "obligation_evidence_by_id": {},
            "compliance_stats": {
                **dict(state.get("compliance_stats") or {}),
                "obligation_compare_ready_count": 0,
            },
        }

    async def stub_section_compare(state, *_args, **_kwargs):
        from review_agent.schemas.section_compare import SectionCompareItem

        item = SectionCompareItem(
            section_id="s1",
            dimension_label="Cap",
            status=ComplianceStatus.NON_COMPLIANT,
            severity=Severity.CRITICAL,
            contract_quote="fees paid",
            policy_quote="fees paid",
            rationale="stub compare",
            confidence=0.9,
        )
        return {
            "section_compare_items": [item.model_dump(mode="json")],
            "compliance_stats": {
                **dict(state.get("compliance_stats") or {}),
                "compliance_mode": "section_first",
            },
            "warnings": [],
        }

    async def stub_obligation_compare(state, *_args, **_kwargs):
        return {
            "obligation_compare_items": [],
            "obligation_findings": [],
            "compliance_stats": dict(state.get("compliance_stats") or {}),
            "warnings": [],
        }

    async def stub_final_gap(state, *_args, **_kwargs):
        return {}

    async def stub_grounding(state, *_args, **_kwargs):
        findings = list(state.get("findings") or [])
        return {"grounded_findings": findings}

    async def stub_report(state, *_args, **_kwargs):
        from document_core.schemas.compliance import ReviewReport

        findings = list(state.get("grounded_findings") or state.get("findings") or [])
        return {
            "report": ReviewReport(
                tenant_id=str(state.get("tenant_id") or "e2e-demo"),
                contract_document_id=uuid4(),
                contract_title="Test",
                findings=findings,
                summary_markdown="ok",
            )
        }

    upstream_on_graph = (
        "load_memory_node",
        "contract_parser_node",
        "clause_detection_node",
        "obligation_extract_node",
        "semantic_route_node",
        "catalog_match_node",
        "contract_routing_node",
        "policy_discovery_node",
        "index_policies_node",
        "save_review_memory_node",
    )
    for name in upstream_on_graph:
        monkeypatch.setattr(rg_mod, name, stub_upstream)

    monkeypatch.setattr(rg_mod, "section_policy_retrieval_node", stub_section_retrieval)
    monkeypatch.setattr(rg_mod, "obligation_retrieval_node", stub_obligation_retrieval)
    monkeypatch.setattr(rg_mod, "evidence_sufficiency_node", stub_evidence)
    monkeypatch.setattr(rg_mod, "section_compare_llm_node", stub_section_compare)
    monkeypatch.setattr(rg_mod, "obligation_compare_node", stub_obligation_compare)
    monkeypatch.setattr(rg_mod, "final_gap_verify_node", stub_final_gap)
    monkeypatch.setattr(rg_mod, "grounding_node", stub_grounding)
    monkeypatch.setattr(rg_mod, "report_node", stub_report)

    assert resolve_pipeline_wiring("e2e-demo", settings) == "parallel_hybrid"

    graph = build_review_graph(AsyncMock(), tenant_id="e2e-demo")
    initial = {
        "tenant_id": "e2e-demo",
        "contract_title": "Test",
        "contract_sections": [],
        "obligations": [{"obligation_id": "o1", "section_id": "s1", "text": "pay fees"}],
        "compliance_stats": {"review_pipeline_wiring": "parallel_hybrid"},
        "findings": [],
        "warnings": [],
        "indexed_policies": [],
        "section_retrieval_by_id": {},
        "section_compare_items": [],
        "obligation_findings": [],
    }

    result = await graph.ainvoke(initial, config={"configurable": {"thread_id": "pg6-test"}})

    assert result.get("findings")
    stats = result.get("compliance_stats") or {}
    assert stats.get("pipeline_join_ready") is True
    findings = result.get("findings") or []
    assert len(findings) >= 1
    first = findings[0]
    status = getattr(first, "status", None) or first.get("status")
    assert str(getattr(status, "value", status)) == "NON_COMPLIANT"


@pytest.mark.asyncio
async def test_section_compare_fail_open(monkeypatch):
    from review_agent.graph.section_compare_nodes import section_compare_llm_node

    monkeypatch.setattr(
        "review_agent.graph.section_compare_nodes.get_settings",
        lambda: ReviewSettings(compare_branch_fail_open=True),
    )

    async def boom(*_args, **_kwargs):
        raise RuntimeError("compare exploded")

    monkeypatch.setattr(
        "review_agent.graph.section_compare_nodes._section_compare_llm_impl",
        boom,
    )

    out = await section_compare_llm_node({"compliance_stats": {}}, AsyncMock())
    assert out["section_compare_items"] == []
    assert out["compliance_stats"]["section_compare_failed"] is True
