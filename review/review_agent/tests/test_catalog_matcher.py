"""Tests for catalog matcher (Phase R3)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from document_core.schemas.policy_catalog import CatalogSearchHit
from review_agent.config import ReviewSettings
from review_agent.schemas.routing_plan import ObligationRoutingPlan
from review_agent.services.catalog_matcher import match_obligation_to_catalog
from review_agent.services.catalog_registry import CatalogEntry

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "routing_golden.json"


def _entry(document_id: str, title: str) -> CatalogEntry:
    return CatalogEntry(
        document_id=document_id,
        policy_ref=title.lower().replace(" ", "-"),
        title=title,
        aliases=[title],
        topics=[],
        summary=title,
    )


@pytest.mark.asyncio
async def test_catalog_match_alias_path():
    client = AsyncMock()
    plan = ObligationRoutingPlan(
        obligation_id="2.3-o0",
        routing_source="registry_alias",
        confidence=1.0,
        resolved_document_ids=["doc-sp"],
    )
    match = await match_obligation_to_catalog(
        plan,
        client=client,
        tenant_id="tenant",
        catalog_entries=[_entry("doc-sp", "Security Practices Policy")],
        allowed_doc_ids={"doc-sp"},
        settings=ReviewSettings(),
    )
    client.search_policy_catalog.assert_not_called()
    assert match.candidate_doc_ids == ["doc-sp"]
    assert match.routing_source == "registry_alias"
    assert match.route_decision == "compare"


@pytest.mark.asyncio
async def test_catalog_match_boilerplate_ipc():
    client = AsyncMock()
    plan = ObligationRoutingPlan(
        obligation_id="10.1-o0",
        routing_source="skipped_boilerplate",
        confidence=0.0,
    )
    match = await match_obligation_to_catalog(
        plan,
        client=client,
        tenant_id="tenant",
        catalog_entries=[],
        allowed_doc_ids=set(),
        settings=ReviewSettings(),
    )
    assert match.route_decision == "ipc"
    assert match.candidate_doc_ids == []
    client.search_policy_catalog.assert_not_called()


@pytest.mark.asyncio
async def test_catalog_match_tenant_fence():
    ir_id = str(uuid4())
    sp_id = str(uuid4())
    client = AsyncMock()
    client.search_policy_catalog.return_value = [
        CatalogSearchHit(document_id=ir_id, title="Incident Response", score=0.9),
        CatalogSearchHit(document_id=sp_id, title="Security Practices Policy", score=0.7),
    ]
    plan = ObligationRoutingPlan(
        obligation_id="2.3-o1",
        intent="security incident notification",
        search_queries=["breach notification"],
        confidence=0.9,
        routing_source="llm",
    )
    match = await match_obligation_to_catalog(
        plan,
        client=client,
        tenant_id="tenant",
        catalog_entries=[_entry(sp_id, "Security Practices Policy")],
        allowed_doc_ids={sp_id},
        settings=ReviewSettings(),
    )
    assert ir_id not in match.candidate_doc_ids
    assert sp_id in match.candidate_doc_ids
    assert any(item["reason"] == "not_in_tenant_registry" for item in match.rejected)


@pytest.mark.asyncio
async def test_catalog_match_union_queries():
    doc_a = str(uuid4())
    doc_b = str(uuid4())

    async def _search(request):
        if "breach" in request.query:
            return [CatalogSearchHit(document_id=doc_a, title="Doc A", score=0.8)]
        return [CatalogSearchHit(document_id=doc_b, title="Doc B", score=0.75)]

    client = AsyncMock()
    client.search_policy_catalog.side_effect = _search
    plan = ObligationRoutingPlan(
        obligation_id="x-o0",
        search_queries=["breach notification", "incident response timeline"],
        confidence=0.9,
        routing_source="llm",
    )
    match = await match_obligation_to_catalog(
        plan,
        client=client,
        tenant_id="tenant",
        catalog_entries=[],
        allowed_doc_ids={doc_a, doc_b},
        settings=ReviewSettings(),
    )
    assert set(match.candidate_doc_ids) == {doc_a, doc_b}
    assert client.search_policy_catalog.await_count == 2


@pytest.mark.asyncio
async def test_governing_law_no_ir():
    client = AsyncMock()
    ir_id = str(uuid4())
    plan = ObligationRoutingPlan(
        obligation_id="10.1-o0",
        routing_source="skipped_boilerplate",
        confidence=0.0,
    )
    match = await match_obligation_to_catalog(
        plan,
        client=client,
        tenant_id="tenant",
        catalog_entries=[_entry(ir_id, "Incident Response")],
        allowed_doc_ids={ir_id},
        settings=ReviewSettings(),
    )
    assert match.route_decision == "ipc"
    assert ir_id not in match.candidate_doc_ids


@pytest.mark.asyncio
async def test_catalog_match_weak_score_expands_when_candidates_exist():
    doc_id = str(uuid4())
    client = AsyncMock()
    client.search_policy_catalog.return_value = [
        CatalogSearchHit(document_id=doc_id, title="Privacy Policy", score=0.18),
    ]
    plan = ObligationRoutingPlan(
        obligation_id="4-o0",
        search_queries=["privacy data collection"],
        confidence=0.9,
        routing_source="llm",
    )
    match = await match_obligation_to_catalog(
        plan,
        client=client,
        tenant_id="tenant",
        catalog_entries=[_entry(doc_id, "Privacy Policy")],
        allowed_doc_ids={doc_id},
        settings=ReviewSettings(catalog_match_min_score=0.25),
    )
    assert doc_id in match.candidate_doc_ids
    assert match.route_decision == "expand"


@pytest.mark.asyncio
async def test_catalog_marginal_score_routes_to_compare():
    """PR-05 — fenced score between 0.85×min and min uses compare."""
    doc_id = str(uuid4())
    client = AsyncMock()
    client.search_policy_catalog.return_value = [
        CatalogSearchHit(document_id=doc_id, title="Privacy Policy", score=0.22),
    ]
    plan = ObligationRoutingPlan(
        obligation_id="4-o0",
        search_queries=["privacy data collection"],
        confidence=0.9,
        routing_source="llm",
    )
    match = await match_obligation_to_catalog(
        plan,
        client=client,
        tenant_id="tenant",
        catalog_entries=[_entry(doc_id, "Privacy Policy")],
        allowed_doc_ids={doc_id},
        settings=ReviewSettings(catalog_match_min_score=0.25),
    )
    assert doc_id in match.candidate_doc_ids
    assert match.route_decision == "compare"


@pytest.mark.asyncio
async def test_catalog_low_confidence_with_explicit_mentions_searches():
    """PR-06 — explicit policy mentions bypass planner IPC preflight."""
    doc_id = str(uuid4())
    client = AsyncMock()
    client.search_policy_catalog.return_value = [
        CatalogSearchHit(document_id=doc_id, title="Privacy Policy", score=0.9),
    ]
    plan = ObligationRoutingPlan(
        obligation_id="4-o0",
        search_queries=["privacy"],
        confidence=0.2,
        explicit_policy_mentions=["Privacy Policy"],
        routing_source="llm",
    )
    match = await match_obligation_to_catalog(
        plan,
        client=client,
        tenant_id="tenant",
        catalog_entries=[_entry(doc_id, "Privacy Policy")],
        allowed_doc_ids={doc_id},
        settings=ReviewSettings(),
    )
    client.search_policy_catalog.assert_called()
    assert doc_id in match.candidate_doc_ids
    assert match.route_decision == "compare"


@pytest.mark.asyncio
async def test_catalog_title_fallback_when_search_empty():
    doc_id = str(uuid4())
    client = AsyncMock()
    client.search_policy_catalog.return_value = []
    plan = ObligationRoutingPlan(
        obligation_id="4-o0",
        intent="data processing",
        search_queries=["vendor obligations"],
        confidence=0.9,
        routing_source="llm",
    )
    match = await match_obligation_to_catalog(
        plan,
        client=client,
        tenant_id="tenant",
        catalog_entries=[_entry(doc_id, "Atlassian Privacy Policy")],
        allowed_doc_ids={doc_id},
        settings=ReviewSettings(catalog_match_obligation_fallback_enabled=True),
        obligation_text="Customer personal data must follow the Atlassian Privacy Policy.",
        section_title="Privacy",
    )
    assert doc_id in match.candidate_doc_ids
    assert match.route_decision in ("expand", "compare")
    assert client.search_policy_catalog.await_count >= 1


def test_routing_golden_fixture_ipc_cases():
    cases = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    ipc_cases = [c for c in cases if c.get("expected_route_decision") == "ipc"]
    assert len(ipc_cases) >= 2
    governing = [c for c in ipc_cases if c["obligation_id"] in ("10.1-o0", "10.5-o0")]
    assert len(governing) == 2
    for case in governing:
        assert case["is_boilerplate"] is True
        assert "Incident Response" in case.get("forbidden_doc_titles", [])
