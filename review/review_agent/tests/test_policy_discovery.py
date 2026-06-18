"""Tests for tenant policy discovery (Phase 6 Pass 2)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from document_core.schemas.chunk import DocumentKind, IngestRequest
from mcp.document_server.main import app
from review_agent.clients.document_client import DocumentMCPClient
from review_agent.config import ReviewSettings
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
                applies_to_contract_types=["msa"],
            )
        )
        settings = ReviewSettings()
        discovered, warnings = await discover_policies_from_topics(
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
        discovered, warnings = await discover_policies_from_topics(
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
        settings = ReviewSettings(discovery_max_policies=1)
        discovered, _ = await discover_policies_from_topics(
            client,
            tenant_id="demo",
            topics=["limitation of liability"],
            contract_type=None,
            policy_type=None,
            settings=settings,
        )

    assert len(discovered) == 1
