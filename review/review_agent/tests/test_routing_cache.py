"""Tests for routing cache (Phase R9)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from review_agent.config import ReviewSettings
from review_agent.schemas.obligation import ContractObligation
from review_agent.schemas.routing_plan import ObligationRoutingPlan
from review_agent.services.catalog_registry import CatalogEntry
from review_agent.services.routing_cache import (
    clear_routing_cache,
    get_cached_plan,
    get_catalog_snapshot,
    plan_cache_key,
    set_cached_plan,
)


def _entry(doc_id: str, title: str) -> CatalogEntry:
    return CatalogEntry(
        document_id=doc_id,
        policy_ref=title.lower().replace(" ", "-"),
        title=title,
        aliases=[title],
        topics=[],
        summary=title,
    )


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_routing_cache()
    yield
    clear_routing_cache()


@pytest.mark.asyncio
async def test_catalog_cache_hit_on_second_load():
    client = AsyncMock()
    client.list_policy_registry.return_value = type(
        "Resp",
        (),
        {"policies": []},
    )()
    settings = ReviewSettings(routing_cache_enabled=True, routing_cache_ttl_seconds=300)

    first = await get_catalog_snapshot(client, "tenant-a", settings=settings)
    second = await get_catalog_snapshot(client, "tenant-a", settings=settings)

    assert first.catalog_version == second.catalog_version
    client.list_policy_registry.assert_called_once()


def test_plan_cache_roundtrip():
    settings = ReviewSettings(routing_cache_enabled=True, routing_plan_cache_max_entries=10)
    ob = ContractObligation(obligation_id="1-o0", section_id="1", text="notify incident")
    key = plan_cache_key(tenant_id="t1", catalog_version="v1", obligation=ob)
    plan = ObligationRoutingPlan(obligation_id=ob.obligation_id, confidence=0.9)
    set_cached_plan(key, plan, settings=settings)
    assert get_cached_plan(key, settings) == plan


def test_plan_cache_lru_eviction():
    settings = ReviewSettings(routing_cache_enabled=True, routing_plan_cache_max_entries=2)
    ob = ContractObligation(obligation_id="1-o0", section_id="1", text="a")
    for index in range(3):
        obligation = ContractObligation(obligation_id=f"{index}-o0", section_id="1", text=f"t{index}")
        key = plan_cache_key(tenant_id="t1", catalog_version="v1", obligation=obligation)
        set_cached_plan(
            key,
            ObligationRoutingPlan(obligation_id=obligation.obligation_id, confidence=0.5),
            settings=settings,
        )
    assert get_cached_plan(plan_cache_key(tenant_id="t1", catalog_version="v1", obligation=ob), settings) is None
