"""Tests for dynamic policy review plan (Phase 1)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from document_core.schemas.chunk import DocumentKind, IngestRequest
from mcp.document_server.main import app
from review_agent.clients.document_client import DocumentMCPClient
from review_agent.config import ReviewSettings
from review_agent.dimensions.loader import load_dimensions, yaml_to_categories
from review_agent.services.policy_plan import build_review_plan, search_queries_from_section
from tests.fixtures import SAMPLE_POLICY


@pytest.mark.asyncio
async def test_build_plan_two_sections_from_sample_policy():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        client = DocumentMCPClient("http://test", http_client=http)
        result = await client.index_policy(
            IngestRequest(
                tenant_id="demo",
                title="Vendor Policy",
                kind=DocumentKind.POLICY,
                text=SAMPLE_POLICY,
                applies_to_contract_types=["msa"],
            )
        )
        indexed = [
            {
                "document_id": str(result.document_id),
                "title": "Vendor Policy",
                "applies_to_contract_types": ["msa"],
            }
        ]
        settings = ReviewSettings(review_plan_mode="dynamic", review_max_categories=30)
        categories, warnings = await build_review_plan(
            client=client,
            tenant_id="demo",
            indexed_policies=indexed,
            policy_document_ids=None,
            contract_type="msa",
            settings=settings,
        )

    assert not warnings
    assert len(categories) == 2
    assert all(c.source == "policy_section" for c in categories)
    labels = {c.label for c in categories}
    assert any("Limitation of Liability" in label for label in labels)
    assert any("Indemnification" in label for label in labels)


@pytest.mark.asyncio
async def test_build_plan_empty_store_warning():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        client = DocumentMCPClient("http://test", http_client=http)
        settings = ReviewSettings()
        categories, warnings = await build_review_plan(
            client=client,
            tenant_id="empty-tenant",
            indexed_policies=[],
            policy_document_ids=None,
            contract_type=None,
            settings=settings,
        )

    assert categories == []
    assert len(warnings) == 1
    assert "No policy documents indexed" in warnings[0]


@pytest.mark.asyncio
async def test_contract_type_filter_skips_non_matching_policy():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        client = DocumentMCPClient("http://test", http_client=http)
        result = await client.index_policy(
            IngestRequest(
                tenant_id="demo",
                title="NDA Policy",
                kind=DocumentKind.POLICY,
                text=SAMPLE_POLICY,
                applies_to_contract_types=["nda"],
            )
        )
        indexed = [
            {
                "document_id": str(result.document_id),
                "title": "NDA Policy",
                "applies_to_contract_types": ["nda"],
            }
        ]
        settings = ReviewSettings()
        categories, _ = await build_review_plan(
            client=client,
            tenant_id="demo",
            indexed_policies=indexed,
            policy_document_ids=None,
            contract_type="msa",
            settings=settings,
        )

    assert categories == []


@pytest.mark.asyncio
async def test_category_cap_warning(monkeypatch):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        client = DocumentMCPClient("http://test", http_client=http)
        result = await client.index_policy(
            IngestRequest(
                tenant_id="demo",
                title="Vendor Policy",
                kind=DocumentKind.POLICY,
                text=SAMPLE_POLICY,
            )
        )
        indexed = [{"document_id": str(result.document_id), "title": "Vendor Policy"}]
        settings = ReviewSettings(review_max_categories=1)
        categories, warnings = await build_review_plan(
            client=client,
            tenant_id="demo",
            indexed_policies=indexed,
            policy_document_ids=None,
            contract_type=None,
            settings=settings,
        )

    assert len(categories) == 1
    assert any("capped at 1" in w for w in warnings)


@pytest.mark.asyncio
async def test_request_scope_ignores_other_tenant_policies():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        client = DocumentMCPClient("http://test", http_client=http)
        result_a = await client.index_policy(
            IngestRequest(
                tenant_id="demo",
                title="Policy A",
                kind=DocumentKind.POLICY,
                text=SAMPLE_POLICY,
            )
        )
        await client.index_policy(
            IngestRequest(
                tenant_id="demo",
                title="Policy B",
                kind=DocumentKind.POLICY,
                text="9. Confidentiality\nAll information shall remain confidential.",
            )
        )
        indexed = [{"document_id": str(result_a.document_id), "title": "Policy A"}]
        settings = ReviewSettings(review_policy_scope="request")
        categories, _ = await build_review_plan(
            client=client,
            tenant_id="demo",
            indexed_policies=indexed,
            policy_document_ids=None,
            contract_type=None,
            settings=settings,
        )

    assert len(categories) == 2
    assert all(str(c.policy_document_id) == str(result_a.document_id) for c in categories)


@pytest.mark.asyncio
async def test_tenant_scope_includes_all_indexed_policies():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        client = DocumentMCPClient("http://test", http_client=http)
        result_a = await client.index_policy(
            IngestRequest(
                tenant_id="demo",
                title="Policy A",
                kind=DocumentKind.POLICY,
                text=SAMPLE_POLICY,
            )
        )
        await client.index_policy(
            IngestRequest(
                tenant_id="demo",
                title="Policy B",
                kind=DocumentKind.POLICY,
                text="9. Confidentiality\nAll information shall remain confidential.",
            )
        )
        indexed = [{"document_id": str(result_a.document_id), "title": "Policy A"}]
        settings = ReviewSettings(review_policy_scope="tenant")
        categories, _ = await build_review_plan(
            client=client,
            tenant_id="demo",
            indexed_policies=indexed,
            policy_document_ids=None,
            contract_type=None,
            settings=settings,
        )

    doc_ids = {str(c.policy_document_id) for c in categories}
    assert len(doc_ids) == 2


@pytest.mark.asyncio
async def test_discovered_scope_never_lists_all_tenant_policies(monkeypatch):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        client = DocumentMCPClient("http://test", http_client=http)
        result_a = await client.index_policy(
            IngestRequest(
                tenant_id="demo",
                title="Policy A",
                kind=DocumentKind.POLICY,
                text=SAMPLE_POLICY,
            )
        )
        await client.index_policy(
            IngestRequest(
                tenant_id="demo",
                title="Policy B",
                kind=DocumentKind.POLICY,
                text="9. Confidentiality\nAll information shall remain confidential.",
            )
        )

        async def _fail_list_policies(_tenant_id: str):
            raise AssertionError("list_policies must not be called for discovered scope")

        monkeypatch.setattr(client, "list_policies", _fail_list_policies)

        indexed = [{"document_id": str(result_a.document_id), "title": "Policy A"}]
        settings = ReviewSettings(review_policy_scope="discovered")
        categories, _ = await build_review_plan(
            client=client,
            tenant_id="demo",
            indexed_policies=indexed,
            policy_document_ids=[str(result_a.document_id)],
            contract_type=None,
            settings=settings,
        )

    assert len(categories) == 2
    assert all(str(c.policy_document_id) == str(result_a.document_id) for c in categories)


def test_static_yaml_to_categories_five_dimensions():
    categories, warnings = yaml_to_categories(load_dimensions())
    assert not warnings
    assert len(categories) == 5
    assert categories[0].source == "yaml_static"
    assert categories[0].policy_document_id is None


def test_search_queries_from_section():
    queries = search_queries_from_section(
        "4. Limitation of Liability",
        "4. Limitation of Liability\nVendor liability shall not exceed fees.",
    )
    assert queries[0] == "4. Limitation of Liability"
    assert "Vendor liability" in queries[1]
