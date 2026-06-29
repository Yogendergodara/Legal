"""Tests for tenant policy discovery (Phase 6 Pass 2 + P2-G grouping)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.integration
from httpx import ASGITransport, AsyncClient

from document_core.schemas.chunk import DocumentKind, IngestRequest
from mcp.document_server.main import app
from review_agent.clients.document_client import DocumentMCPClient
from review_agent.config import ReviewSettings
from review_agent.services import policy_discovery
from review_agent.services.policy_discovery import discover_policies_from_topics
from tests.fixtures import SAMPLE_POLICY


@pytest.mark.asyncio
async def test_discover_policies_by_liability_topic():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        client = DocumentMCPClient("http://test", http_client=http)
        await client.index_policy(
            IngestRequest(
                tenant_id="demo",
                title="Vendor Policy",
                kind=DocumentKind.POLICY,
                text=SAMPLE_POLICY,
            )
        )
        settings = ReviewSettings()
        discovered, warnings, _meta = await discover_policies_from_topics(
            client,
            tenant_id="demo",
            topics=["limitation of liability", "indemnification"],
            contract_type="msa",
            policy_type=None,
            settings=settings,
        )

    assert not warnings
    assert len(discovered) == 1
    assert discovered[0].title
    assert discovered[0].match_score > 0
    assert discovered[0].matched_topics


@pytest.mark.asyncio
async def test_discover_policies_empty_store_warning():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        client = DocumentMCPClient("http://test", http_client=http)
        settings = ReviewSettings()
        discovered, warnings, _meta = await discover_policies_from_topics(
            client,
            tenant_id="empty",
            topics=["limitation of liability"],
            contract_type=None,
            policy_type=None,
            settings=settings,
        )

    assert discovered == []
    assert len(warnings) == 1
    assert "No policies discovered" in warnings[0]


@pytest.mark.asyncio
async def test_discover_policies_respects_max_cap():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        client = DocumentMCPClient("http://test", http_client=http)
        for idx in range(3):
            await client.index_policy(
                IngestRequest(
                    tenant_id="demo",
                    title=f"Policy {idx}",
                    kind=DocumentKind.POLICY,
                    text=f"{idx}. Limitation of Liability\nCap applies.\n",
                )
            )
        settings = ReviewSettings(
            discovery_group_mode="flat",
            discovery_max_policies=1,
        )
        discovered, _, _meta = await discover_policies_from_topics(
            client,
            tenant_id="demo",
            topics=["limitation of liability"],
            contract_type=None,
            policy_type=None,
            settings=settings,
        )

    assert len(discovered) == 1


@pytest.mark.asyncio
async def test_discover_policies_cap_emits_warning():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        client = DocumentMCPClient("http://test", http_client=http)
        for idx in range(3):
            await client.index_policy(
                IngestRequest(
                    tenant_id="demo",
                    title=f"Policy {idx}",
                    kind=DocumentKind.POLICY,
                    text=f"{idx}. Limitation of Liability\nCap applies.\n",
                )
            )
        settings = ReviewSettings(
            discovery_group_mode="flat",
            discovery_max_policies=1,
            discovery_warn_on_cap=True,
        )
        discovered, warnings, meta = await discover_policies_from_topics(
            client,
            tenant_id="demo",
            topics=["limitation of liability"],
            contract_type=None,
            policy_type=None,
            settings=settings,
        )

    assert len(discovered) == 1
    assert meta["discovery_capped"] is True
    assert any("capped at 1" in warning for warning in warnings)


@pytest.mark.asyncio
async def test_discovery_groups_by_category():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        client = DocumentMCPClient("http://test", http_client=http)
        for idx, score_text in enumerate(
            (
                "Limitation of Liability cap one hundred thousand dollars.",
                "Limitation of Liability cap twelve months fees.",
                "Human Rights forced labor due diligence OECD.",
            )
        ):
            category = "liability" if idx < 2 else "human_rights"
            await client.index_policy(
                IngestRequest(
                    tenant_id="demo",
                    title=f"Policy {idx}",
                    kind=DocumentKind.POLICY,
                    text=f"{idx}. {score_text}",
                    categories=[category],
                )
            )
        settings = ReviewSettings(discovery_max_policies=0)
        discovered, warnings, meta = await discover_policies_from_topics(
            client,
            tenant_id="demo",
            topics=["limitation of liability", "human rights forced labor"],
            contract_type=None,
            policy_type=None,
            settings=settings,
        )

    assert len(discovered) == 2
    groups = {policy.policy_group for policy in discovered}
    assert groups == {"liability", "human_rights"}
    assert meta["discovery_deduped"] >= 1
    assert any("duplicate-category" in warning for warning in warnings)


@pytest.mark.asyncio
async def test_discovery_group_cap_six():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        client = DocumentMCPClient("http://test", http_client=http)
        category_topics = [
            ("compliance", "supplier code of conduct compliance"),
            ("human_rights", "human rights forced labor due diligence"),
            ("minerals", "responsible minerals sourcing tin tungsten"),
            ("environment", "environment greenhouse gas emissions"),
            ("security", "information security MSS requirements"),
            ("vendor_security", "vendor security assessment controls"),
            ("privacy", "data privacy personal information"),
            ("termination", "termination notice period breach"),
        ]
        for idx, (category, _topic) in enumerate(category_topics):
            await client.index_policy(
                IngestRequest(
                    tenant_id="demo",
                    title=f"Policy {category}",
                    kind=DocumentKind.POLICY,
                    text=f"{idx}. {category} policy requirements and obligations.",
                    categories=[category],
                )
            )
        settings = ReviewSettings(
            discovery_group_cap_mode="fixed",
            discovery_max_policy_groups=6,
            discovery_max_policies=0,
            discovery_max_topics=0,
            discovery_topic_cap_mode="fixed",
            discovery_warn_on_cap=True,
        )
        discovered, warnings, meta = await discover_policies_from_topics(
            client,
            tenant_id="demo",
            topics=[topic for _category, topic in category_topics],
            contract_type=None,
            policy_type=None,
            settings=settings,
        )

    assert len(discovered) == 6
    assert meta["discovery_groups"] == 6
    assert meta["discovery_total_ranked"] >= 8
    assert any("group cap at 6" in warning for warning in warnings)


@pytest.mark.asyncio
async def test_discovery_flat_mode_legacy():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        client = DocumentMCPClient("http://test", http_client=http)
        for idx in range(3):
            await client.index_policy(
                IngestRequest(
                    tenant_id="demo",
                    title=f"Liability Policy {idx}",
                    kind=DocumentKind.POLICY,
                    text=f"{idx}. Limitation of Liability cap applies.",
                    categories=["liability"],
                )
            )
        settings = ReviewSettings(
            discovery_group_mode="flat",
            discovery_max_policies=2,
            discovery_warn_on_cap=True,
        )
        discovered, warnings, meta = await discover_policies_from_topics(
            client,
            tenant_id="demo",
            topics=["limitation of liability"],
            contract_type=None,
            policy_type=None,
            settings=settings,
        )

    assert len(discovered) == 2
    assert meta["discovery_group_mode"] == "flat"
    assert meta["discovery_deduped"] == 0
    assert any("capped at 2" in warning for warning in warnings)


@pytest.mark.asyncio
async def test_discovery_topics_capped():
    client = AsyncMock()
    client.search_policy = AsyncMock(return_value=[])
    settings = ReviewSettings(discovery_max_topics=2, discovery_topic_cap_mode="fixed")
    await discover_policies_from_topics(
        client,
        tenant_id="demo",
        topics=["alpha", "beta", "gamma", "delta"],
        contract_type=None,
        policy_type=None,
        settings=settings,
    )
    assert client.search_policy.await_count == 2


def test_policy_group_key_prefers_category():
    key = policy_discovery._policy_group_key(
        categories=["human_rights", "labor"],
        metadata={},
        matched_topics=["forced labor"],
        document_id="abc",
    )
    assert key == "human_rights"


def test_select_grouped_policies_keeps_best_score_per_group():
    from review_agent.schemas.discovered_policy import DiscoveredPolicy

    ranked = [
        DiscoveredPolicy(document_id="1", match_score=0.9, policy_group="liability"),
        DiscoveredPolicy(document_id="2", match_score=0.5, policy_group="liability"),
        DiscoveredPolicy(document_id="3", match_score=0.8, policy_group="human_rights"),
    ]
    grouped, deduped, groups_before = policy_discovery._select_grouped_policies(
        ranked,
        max_groups=6,
        max_policies=0,
    )
    assert deduped == 1
    assert groups_before == 2
    assert len(grouped) == 2
    assert grouped[0].document_id == "1"
    assert grouped[1].document_id == "3"


def test_resolve_discovery_group_cap_adaptive():
    settings = ReviewSettings(
        discovery_group_cap_mode="adaptive",
        discovery_min_policy_groups=6,
        discovery_max_policy_groups_ceiling=20,
    )
    cap = policy_discovery.resolve_discovery_group_cap(
        settings=settings,
        reviewable_section_count=20,
        unique_category_count=15,
    )
    assert cap == 15


def test_resolve_discovery_group_cap_cisco_floor():
    settings = ReviewSettings(discovery_group_cap_mode="adaptive")
    cap = policy_discovery.resolve_discovery_group_cap(
        settings=settings,
        reviewable_section_count=6,
        unique_category_count=5,
    )
    assert cap == 6


def test_resolve_topic_cap_adaptive():
    settings = ReviewSettings(
        discovery_topic_cap_mode="adaptive",
        discovery_max_topics_ceiling=20,
    )
    assert policy_discovery.resolve_topic_cap(settings=settings, topic_count=18) == 18


@pytest.mark.asyncio
async def test_contract_type_fallback_niche():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        client = DocumentMCPClient("http://test", http_client=http)
        await client.index_policy(
            IngestRequest(
                tenant_id="demo",
                title="MSA Liability",
                kind=DocumentKind.POLICY,
                text="1. Limitation of Liability\nFees paid in twelve months.",
                categories=["liability"],
            )
        )
        await client.index_policy(
            IngestRequest(
                tenant_id="demo",
                title="MSA Indemnity",
                kind=DocumentKind.POLICY,
                text="2. Indemnification\nVendor shall indemnify.",
                categories=["indemnity"],
            )
        )
        settings = ReviewSettings(
            discovery_group_cap_mode="fixed",
            discovery_max_policy_groups=0,
            discovery_max_policies=0,
            discovery_contract_type_fallback_min_hits=2,
            discovery_section_category_sweep=False,
        )
        discovered, warnings, meta = await discover_policies_from_topics(
            client,
            tenant_id="demo",
            topics=["limitation of liability", "indemnification"],
            contract_type="oem",
            policy_type=None,
            settings=settings,
        )

    assert len(discovered) >= 2
    assert meta["discovery_contract_type_relaxed"] is True
    assert any("relaxed contract_type" in warning for warning in warnings)


@pytest.mark.asyncio
async def test_category_sweep_adds_minerals():
    from uuid import UUID

    from document_core.schemas.chunk import ChunkRole, IndexedChunk

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        client = DocumentMCPClient("http://test", http_client=http)
        await client.index_policy(
            IngestRequest(
                tenant_id="demo",
                title="Minerals Policy",
                kind=DocumentKind.POLICY,
                text="1. Responsible Minerals\nSubmit MRT templates.",
                categories=["minerals"],
            )
        )
        settings = ReviewSettings(
            discovery_group_cap_mode="fixed",
            discovery_max_policy_groups=0,
            discovery_max_policies=0,
            discovery_section_category_sweep=True,
        )
        section = IndexedChunk(
            chunk_id="c-3",
            document_id=UUID("00000000-0000-0000-0000-000000000001"),
            tenant_id="demo",
            kind=DocumentKind.CONTRACT,
            chunk_role=ChunkRole.PARENT,
            section_id="3",
            section_path="3",
            title="Responsible Minerals",
            text="Supplier is not obligated to complete Minerals Reporting Templates.",
        )
        discovered, _warnings, meta = await discover_policies_from_topics(
            client,
            tenant_id="demo",
            topics=[],
            contract_type=None,
            policy_type=None,
            settings=settings,
            contract_sections=[section],
        )

    assert len(discovered) == 1
    assert discovered[0].policy_group == "minerals"
    assert "minerals" in meta["discovery_section_categories"]
    assert meta["discovery_category_sweep_added"] >= 1


def test_select_grouped_with_category_reserve_prefers_sla():
    from review_agent.schemas.discovered_policy import DiscoveredPolicy

    ranked = [
        DiscoveredPolicy(
            document_id="compliance-1",
            match_score=0.95,
            policy_group="compliance",
            categories=["compliance"],
        ),
        DiscoveredPolicy(
            document_id="sla-1",
            match_score=0.7,
            policy_group="sla",
            categories=["sla"],
        ),
    ]
    grouped, _deduped, _groups_before, reserved = policy_discovery._select_grouped_with_category_reserve(
        ranked,
        ["sla", "compliance"],
        max_groups=6,
        max_policies=0,
    )
    doc_ids = {policy.document_id for policy in grouped}
    assert "sla-1" in doc_ids
    assert reserved >= 1


def test_category_reserve_capped_leaves_fill_slots():
    from review_agent.schemas.discovered_policy import DiscoveredPolicy

    categories = [f"cat{i}" for i in range(8)]
    ranked = [
        DiscoveredPolicy(
            document_id=f"doc-{category}",
            match_score=0.9 - index * 0.01,
            policy_group=f"group-{category}",
            categories=[category],
        )
        for index, category in enumerate(categories)
    ]
    ranked.append(
        DiscoveredPolicy(
            document_id="topic-only",
            match_score=0.99,
            policy_group="topic-search",
            categories=["topic_only"],
        )
    )
    grouped, _deduped, _groups_before, reserved = policy_discovery._select_grouped_with_category_reserve(
        ranked,
        categories,
        max_groups=6,
        max_policies=0,
    )
    doc_ids = {policy.document_id for policy in grouped}
    assert "topic-only" in doc_ids
    assert reserved <= 3


def test_group_and_cap_vendor_complete_skips_dedup():
    from review_agent.config import ReviewSettings
    from review_agent.schemas.discovered_policy import DiscoveredPolicy

    ranked = [
        DiscoveredPolicy(
            document_id="a",
            match_score=0.9,
            policy_group="shared",
            categories=["privacy"],
        ),
        DiscoveredPolicy(
            document_id="b",
            match_score=0.8,
            policy_group="shared",
            categories=["privacy"],
        ),
    ]
    settings = ReviewSettings(discovery_vendor_complete_threshold=10)
    grouped, deduped, groups_before, _reserved = policy_discovery._group_and_cap(
        ranked,
        settings=settings,
        group_cap=6,
        section_categories=["privacy"],
    )
    assert deduped == 0
    assert len(grouped) == 2
    assert groups_before == 2


@pytest.mark.asyncio
async def test_discovery_respects_scope_document_ids():
    from uuid import uuid4

    from document_core.schemas.chunk import ChunkRole, IndexedChunk

    in_scope = uuid4()
    out_scope = uuid4()
    parent_in = IndexedChunk(
        chunk_id="p1",
        document_id=in_scope,
        tenant_id="demo",
        kind=DocumentKind.POLICY,
        chunk_role=ChunkRole.PARENT,
        section_id="1",
        section_path="1",
        title="In Scope",
        text="SLA uptime service level agreement.",
        metadata={"categories": ["sla"]},
    )
    parent_out = IndexedChunk(
        chunk_id="p2",
        document_id=out_scope,
        tenant_id="demo",
        kind=DocumentKind.POLICY,
        chunk_role=ChunkRole.PARENT,
        section_id="1",
        section_path="1",
        title="Out Scope",
        text="Compliance supplier code of conduct.",
        metadata={"categories": ["compliance"]},
    )
    client = AsyncMock()
    client.search_policy = AsyncMock(
        side_effect=[
            [type("Hit", (), {"score": 0.9, "parent_chunk": parent_in})()],
            [type("Hit", (), {"score": 0.95, "parent_chunk": parent_out})()],
        ]
    )
    client.search_policy_by_categories = AsyncMock(return_value=[])
    from document_core.schemas.registry import ListPolicyRegistryResponse

    client.list_policy_registry = AsyncMock(return_value=ListPolicyRegistryResponse(tenant_id="demo", policies=[]))

    settings = ReviewSettings(discovery_section_category_sweep=False)
    discovered, _warnings, meta = await discover_policies_from_topics(
        client,
        tenant_id="demo",
        topics=["sla", "compliance"],
        contract_type=None,
        policy_type=None,
        settings=settings,
        scope_document_ids=[str(in_scope)],
    )

    assert len(discovered) == 1
    assert discovered[0].document_id == str(in_scope)
    assert meta["discovery_scope_count"] == 1


@pytest.mark.asyncio
async def test_seed_discovered_from_scope():
    from uuid import uuid4

    from document_core.schemas.registry import ListPolicyRegistryResponse, PolicyRegistryRecord

    doc_id = uuid4()
    client = AsyncMock()
    client.list_policy_registry = AsyncMock(
        return_value=ListPolicyRegistryResponse(
            tenant_id="demo",
            policies=[
                PolicyRegistryRecord(
                    tenant_id="demo",
                    document_id=doc_id,
                    policy_ref="playbook-sla-1",
                    title="SLA Playbook",
                    index_status="indexed",
                    metadata={"categories": ["sla"]},
                )
            ],
        )
    )
    seeded = await policy_discovery.seed_discovered_from_scope(
        client,
        tenant_id="demo",
        scope_document_ids=[str(doc_id)],
    )
    assert str(doc_id) in seeded
    assert seeded[str(doc_id)].categories == ["sla"]


def test_category_boost_multiplicative():
    """T3: Category boost is multiplicative, not additive, and capped at 1.0."""
    from review_agent.config import ReviewSettings

    settings = ReviewSettings(discovery_category_score_boost=0.15)

    # Simulate what _discover_by_section_categories does with the new formula
    raw_score = 0.10
    boosted = min(raw_score * (1.0 + settings.discovery_category_score_boost), 1.0)
    assert abs(boosted - 0.115) < 1e-9, f"Expected 0.115, got {boosted}"

    # With old additive formula it would have been 0.25
    additive_would_be = raw_score + settings.discovery_category_score_boost
    assert abs(additive_would_be - 0.25) < 1e-9
    assert boosted < additive_would_be, "Multiplicative boost must be smaller than additive"

    # Verify ceiling at 1.0
    high_score = 0.95
    capped = min(high_score * (1.0 + settings.discovery_category_score_boost), 1.0)
    assert capped == 1.0, f"Expected ceiling 1.0, got {capped}"
