"""Tests for IPC-1 planner deferral on oversized discovery scope (RC-08)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from review_agent.config import ReviewSettings
from review_agent.graph.routing_nodes import semantic_route_node
from review_agent.schemas.obligation import ContractObligation
from review_agent.services.catalog_registry import CatalogEntry


def _catalog_entry(n: int) -> CatalogEntry:
    doc_id = str(uuid4())
    return CatalogEntry(
        document_id=doc_id,
        policy_ref=f"policy-{n}",
        title=f"Policy {n}",
        aliases=[f"Policy {n}"],
        topics=["compliance"],
        summary="",
    )


@pytest.mark.asyncio
async def test_planner_deferred_when_discovery_scope_exceeds_cap():
    catalog = [_catalog_entry(i) for i in range(20)]
    discovered = [entry.document_id for entry in catalog]
    state = {
        "tenant_id": "atlassian-demo",
        "contract_type": "saas",
        "obligations": [
            ContractObligation(
                obligation_id="1-o1",
                section_id="1",
                text="Notify within 8 hours of any security incident.",
            ).model_dump(mode="json")
        ],
        "discovered_policy_document_ids": discovered,
        "compliance_stats": {},
    }
    settings = ReviewSettings(
        obligation_routing_enabled=True,
        obligation_routing_tenant_allowlist="atlassian-demo",
        routing_planner_max_catalog_policies=12,
        semantic_planner_enabled=True,
    )
    snapshot = AsyncMock()
    snapshot.entries = catalog
    snapshot.catalog_version = "v1"

    with (
        patch("review_agent.graph.routing_nodes.get_settings", return_value=settings),
        patch(
            "review_agent.graph.routing_nodes.get_catalog_snapshot",
            new=AsyncMock(return_value=snapshot),
        ),
        patch(
            "review_agent.graph.routing_nodes.plan_obligation_routing",
            new_callable=AsyncMock,
        ) as planner,
    ):
        await semantic_route_node(state, AsyncMock())
        planner.assert_not_called()


@pytest.mark.asyncio
async def test_planner_runs_on_request_scoped_catalog():
    catalog = [_catalog_entry(i) for i in range(20)]
    request_ids = [entry.document_id for entry in catalog[:9]]
    state = {
        "tenant_id": "atlassian-demo",
        "contract_type": "saas",
        "obligations": [
            ContractObligation(
                obligation_id="1-o1",
                section_id="1",
                text="Notify within 8 hours of any security incident.",
            ).model_dump(mode="json")
        ],
        "policy_document_ids": request_ids,
        "compliance_stats": {},
    }
    settings = ReviewSettings(
        obligation_routing_enabled=True,
        obligation_routing_tenant_allowlist="atlassian-demo",
        routing_planner_max_catalog_policies=12,
        semantic_planner_enabled=True,
    )
    snapshot = AsyncMock()
    snapshot.entries = catalog
    snapshot.catalog_version = "v1"

    with (
        patch("review_agent.graph.routing_nodes.get_settings", return_value=settings),
        patch(
            "review_agent.graph.routing_nodes.get_catalog_snapshot",
            new=AsyncMock(return_value=snapshot),
        ),
        patch(
            "review_agent.graph.routing_nodes.plan_obligation_routing",
            new_callable=AsyncMock,
            return_value={},
        ) as planner,
    ):
        await semantic_route_node(state, AsyncMock())
        planner.assert_called_once()
