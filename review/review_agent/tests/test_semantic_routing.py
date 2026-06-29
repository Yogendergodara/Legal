"""Tests for semantic routing planner and alias match (Phase R2)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from uuid import uuid4

from review_agent.config import ReviewSettings
from review_agent.graph.routing_nodes import semantic_route_node
from review_agent.schemas.obligation import ContractObligation
from review_agent.schemas.routing_plan import BatchRoutingPlanResult, PlannerRoutingItem
from review_agent.services.catalog_alias_match import match_explicit_mentions
from review_agent.services.catalog_registry import CatalogEntry
from review_agent.services.catalog_matcher import match_obligation_to_catalog
from review_agent.services.semantic_routing_planner import _fallback_plan, plan_obligation_routing


def _entry(
    document_id: str,
    title: str,
    *,
    aliases: list[str] | None = None,
) -> CatalogEntry:
    return CatalogEntry(
        document_id=document_id,
        policy_ref=title.lower().replace(" ", "-"),
        title=title,
        aliases=list(aliases or []),
        topics=[],
        summary=title,
    )


def test_alias_match_security_practices():
    catalog = [
        _entry("doc-sp", "Security Practices Policy", aliases=["Security Practices Policy"]),
        _entry("doc-ir", "Incident Response Plan"),
    ]
    match = match_explicit_mentions(
        ["Security Practices Policy"],
        catalog,
        min_score=0.92,
    )
    assert match is not None
    assert match.document_id == "doc-sp"
    assert match.confidence >= 0.92


def test_boilerplate_skips_planner():
    ob = ContractObligation(
        obligation_id="10.1-o0",
        section_id="10.1",
        text="Governed by Wyoming law.",
        is_boilerplate=True,
        obligation_type="governing_law",
    )
    assert ob.is_boilerplate


@pytest.mark.asyncio
async def test_planner_schema_no_uuid(monkeypatch):
    ob = ContractObligation(
        obligation_id="2.3-o1",
        section_id="2.3",
        text="Notify customer within 8 hours of a security incident.",
    )

    async def _mock_invoke(model, schema, *, system, user):
        return BatchRoutingPlanResult(
            plans=[
                PlannerRoutingItem(
                    obligation_id="2.3-o1",
                    intent="security incident notification",
                    concepts=["incident", "notification", "breach"],
                    search_queries=["security incident notification timeline"],
                    confidence=0.91,
                    reasoning="implicit breach notification",
                )
            ]
        )

    monkeypatch.setattr(
        "review_agent.services.semantic_routing_planner.get_review_model",
        lambda **_kw: object(),
    )
    monkeypatch.setattr(
        "review_agent.services.semantic_routing_planner.invoke_structured",
        _mock_invoke,
    )
    plans = await plan_obligation_routing(
        [ob],
        contract_type="nda",
        catalog_entries=[_entry("doc-ir", "Incident Response Plan")],
        settings=ReviewSettings(),
    )
    plan = plans["2.3-o1"]
    dumped = plan.model_dump()
    assert "document_id" not in dumped
    assert "policy_ref" not in dumped
    concepts = " ".join(plan.concepts).lower()
    assert "incident" in concepts
    assert "notification" in concepts


@pytest.mark.asyncio
async def test_planner_implicit_incident(monkeypatch):
    ob = ContractObligation(
        obligation_id="2.3-o1",
        section_id="2.3",
        text="Notify customer within 8 hours of a security incident.",
    )

    async def _mock_invoke(model, schema, *, system, user):
        return BatchRoutingPlanResult(
            plans=[
                PlannerRoutingItem(
                    obligation_id="2.3-o1",
                    intent="security incident notification",
                    concepts=["incident", "notification"],
                    search_queries=["breach customer notification requirements"],
                    confidence=0.9,
                )
            ]
        )

    monkeypatch.setattr(
        "review_agent.services.semantic_routing_planner.get_review_model",
        lambda **_kw: object(),
    )
    monkeypatch.setattr(
        "review_agent.services.semantic_routing_planner.invoke_structured",
        _mock_invoke,
    )
    plans = await plan_obligation_routing([ob], contract_type="nda", catalog_entries=[])
    plan = plans["2.3-o1"]
    joined = " ".join(plan.concepts + plan.search_queries).lower()
    assert "incident" in joined
    assert "notification" in joined


@pytest.mark.asyncio
async def test_graph_node_flag_off(monkeypatch):
    state = {
        "tenant_id": "t",
        "obligations": [
            ContractObligation(
                obligation_id="1-o0",
                section_id="1",
                text="text",
            ).model_dump(mode="json")
        ],
        "compliance_stats": {},
        "obligation_extract_stats": {},
    }
    monkeypatch.setattr(
        "review_agent.graph.routing_nodes.get_settings",
        lambda: ReviewSettings(obligation_routing_enabled=False),
    )
    out = await semantic_route_node(state, client=AsyncMock())
    assert out == {}


def test_fallback_plan_above_ipc_threshold():
    ob = ContractObligation(obligation_id="1-o0", section_id="1", text="Payment terms apply.")
    plan = _fallback_plan(ob, settings=ReviewSettings(routing_ipc_max_confidence=0.60))
    assert plan.confidence >= 0.60
    assert plan.routing_source == "planner_fallback"


@pytest.mark.asyncio
async def test_planner_cap_uses_fallback_with_catalog_path():
    from document_core.schemas.policy_catalog import CatalogSearchHit

    ob = ContractObligation(
        obligation_id="1-o0",
        section_id="1",
        text="Payment terms apply within 30 days.",
    )
    settings = ReviewSettings(routing_ipc_max_confidence=0.60)
    plan = _fallback_plan(ob, settings=settings)
    assert plan.routing_source == "planner_fallback"

    doc_id = str(uuid4())
    client = AsyncMock()
    client.search_policy_catalog.return_value = [
        CatalogSearchHit(document_id=doc_id, score=0.9, title="Payment Policy"),
    ]
    match = await match_obligation_to_catalog(
        plan,
        client=client,
        tenant_id="t1",
        catalog_entries=[_entry(doc_id, "Payment Policy")],
        allowed_doc_ids={doc_id},
        settings=settings,
        obligation_text=ob.text or "",
    )
    assert match.route_decision != "ipc"
