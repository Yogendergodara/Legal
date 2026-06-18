"""Tests for policy retrieval ladder and catalog fetch (Phase 2)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from document_core.schemas.chunk import DocumentKind, IngestRequest, ListSectionsRequest
from mcp.document_server.main import app
from review_agent.clients.document_client import DocumentMCPClient
from review_agent.clients.policy_catalog import (
    PolicyDocument,
    StubPolicyCatalogClient,
    set_policy_catalog,
    stable_policy_document_id,
)
from review_agent.config import ReviewSettings
from review_agent.graph.review_graph import run_review
from review_agent.schemas.review_category import ReviewCategory
from review_agent.services.policy_retrieval import resolve_policy_hits
from tests.fixtures import SAMPLE_CONTRACT, SAMPLE_POLICY


@pytest.fixture(autouse=True)
def catalog_reset():
    set_policy_catalog(None)
    yield
    set_policy_catalog(None)


@pytest.mark.asyncio
async def test_exact_get_section_fast_path():
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
        sections = await client.list_sections(
            ListSectionsRequest(
                tenant_id="demo",
                document_id=result.document_id,
                kind=DocumentKind.POLICY,
            )
        )
        section = sections[0]
        category = ReviewCategory(
            category_id=f"{result.document_id}:{section.section_id}",
            label=section.title,
            policy_document_id=result.document_id,
            policy_section_id=section.section_id,
            search_queries=[section.title],
        )
        settings = ReviewSettings()
        policy_hits, contract_hits, meta = await resolve_policy_hits(
            client=client,
            catalog=None,
            tenant_id="demo",
            category=category,
            contract_document_id=uuid4(),
            contract_type=None,
            policy_type=None,
            fetched_refs=set(),
            policy_ref_by_doc={},
            settings=settings,
        )

    assert meta["retrieval_method"] == "exact"
    assert len(policy_hits) == 1
    assert policy_hits[0].score == 1.0
    assert contract_hits == []


@pytest.mark.asyncio
async def test_fetch_on_miss_via_policy_refs():
    policy_ref = "catalog-vendor-msa"
    tenant_id = "demo"
    doc_id = stable_policy_document_id(tenant_id, policy_ref)

    stub = StubPolicyCatalogClient()
    stub.register(
        PolicyDocument(
            ref=policy_ref,
            title="Catalog Vendor Policy",
            text=SAMPLE_POLICY,
            document_id=doc_id,
            applies_to_contract_types=["msa"],
        )
    )
    set_policy_catalog(stub)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        client = DocumentMCPClient("http://test", http_client=http)
        result = await run_review(
            client=client,
            tenant_id=tenant_id,
            contract_text=SAMPLE_CONTRACT,
            contract_title="Vendor MSA",
            policy_refs=[policy_ref],
            contract_type="msa",
        )

    assert policy_ref in (result.get("fetched_policy_refs") or [])
    assert len(result.get("review_categories") or []) == 2
    report = result["report"]
    assert report is not None
    assert report.findings


@pytest.mark.asyncio
async def test_no_double_catalog_fetch():
    policy_ref = "catalog-once"
    tenant_id = "demo"
    fetch_count = 0

    class CountingStub(StubPolicyCatalogClient):
        async def fetch_policy(self, tenant_id: str, policy_ref: str) -> PolicyDocument | None:
            nonlocal fetch_count
            fetch_count += 1
            return await super().fetch_policy(tenant_id, policy_ref)

    stub = CountingStub(
        {
            policy_ref: PolicyDocument(
                ref=policy_ref,
                title="Policy",
                text=SAMPLE_POLICY,
                document_id=stable_policy_document_id(tenant_id, policy_ref),
            )
        }
    )
    set_policy_catalog(stub)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        client = DocumentMCPClient("http://test", http_client=http)
        await run_review(
            client=client,
            tenant_id=tenant_id,
            contract_text=SAMPLE_CONTRACT,
            policy_refs=[policy_ref],
        )

    assert fetch_count == 1


@pytest.mark.asyncio
async def test_policy_ref_skipped_without_catalog():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        client = DocumentMCPClient("http://test", http_client=http)
        result = await run_review(
            client=client,
            tenant_id="demo",
            contract_text=SAMPLE_CONTRACT,
            policy_refs=["missing-catalog-ref"],
        )

    warnings = result.get("warnings") or []
    assert any("no catalog configured" in w for w in warnings)


@pytest.mark.asyncio
async def test_all_policy_miss_insufficient_context():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        client = DocumentMCPClient("http://test", http_client=http)
        await client.ingest_document(
            IngestRequest(
                tenant_id="demo",
                title="Contract",
                kind=DocumentKind.CONTRACT,
                text=SAMPLE_CONTRACT,
            )
        )
        category = ReviewCategory(
            category_id="static:liability",
            label="Limitation of Liability",
            search_queries=["limitation of liability"],
            source="yaml_static",
        )
        settings = ReviewSettings()
        policy_hits, _, meta = await resolve_policy_hits(
            client=client,
            catalog=None,
            tenant_id="demo",
            category=category,
            contract_document_id=uuid4(),
            contract_type=None,
            policy_type=None,
            fetched_refs=set(),
            policy_ref_by_doc={},
            settings=settings,
        )

    assert policy_hits == []
    assert meta["retrieval_method"] == "none"
