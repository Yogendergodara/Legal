"""Registry and catalog sync tests (PostgreSQL)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from document_core.schemas.chunk import DocumentKind, IngestRequest
from document_core.schemas.registry import RegisterPolicyRequest, SyncPolicyFromCatalogRequest
from document_core.services.catalog_sync import sync_policy_from_catalog
from document_core.services.ingest import ingest_document
from document_core.services.registry import get_policy_by_ref, register_policy, stable_policy_document_id
from document_core.services.search import search_policy
from document_core.schemas.chunk import SearchRequest
from document_core.store.pgvector_store import PgVectorDocumentStore


@pytest.mark.asyncio
async def test_register_policy_pending(store: PgVectorDocumentStore):
    record = register_policy(
        RegisterPolicyRequest(
            tenant_id="t1",
            policy_ref="drive:file-1",
            title="Vendor Policy",
            policy_type="vendor",
        ),
        store=store,
    )
    assert record.index_status == "pending"
    assert record.policy_ref == "drive:file-1"
    assert record.content_hash is None

    fetched = get_policy_by_ref("t1", "drive:file-1", store=store)
    assert fetched is not None
    assert fetched.document_id == record.document_id


@pytest.mark.asyncio
async def test_index_updates_registry_status(store: PgVectorDocumentStore):
    tenant = "t2"
    policy_ref = "confluence:page-9"
    document_id = stable_policy_document_id(tenant, policy_ref)
    register_policy(
        RegisterPolicyRequest(
            tenant_id=tenant,
            policy_ref=policy_ref,
            title="DPA",
            document_id=document_id,
            source="confluence",
        ),
        store=store,
    )
    await ingest_document(
        IngestRequest(
            tenant_id=tenant,
            document_id=document_id,
            title="DPA",
            kind=DocumentKind.POLICY,
            text="4. Limitation of Liability\nVendor liability shall not exceed fees paid.",
            metadata={"policy_ref": policy_ref},
        ),
        store=store,
    )
    row = get_policy_by_ref(tenant, policy_ref, store=store)
    assert row is not None
    assert row.index_status == "indexed"
    assert row.content_hash is not None

    hits = await search_policy(
        SearchRequest(tenant_id=tenant, query="limitation of liability", kind=DocumentKind.POLICY),
        store=store,
    )
    assert hits


@pytest.mark.asyncio
async def test_sync_policy_from_catalog(store: PgVectorDocumentStore):
    tenant = "t3"
    policy_ref = "drive:sync-1"
    document_id = stable_policy_document_id(tenant, policy_ref)

    class FakePayload:
        title = "Synced Policy"
        text = "5. Indemnification\nVendor shall indemnify Customer."
        policy_type = "vendor"
        applies_to_contract_types: list[str] = []
        document_id = document_id
        metadata = {"source": "google_drive"}

    with patch(
        "document_core.services.catalog_sync.fetch_policy_from_catalog",
        new=AsyncMock(return_value=FakePayload()),
    ):
        result = await sync_policy_from_catalog(
            SyncPolicyFromCatalogRequest(tenant_id=tenant, policy_ref=policy_ref),
            catalog_url="http://catalog.example/api/v1",
            store=store,
        )

    assert result.document_id == document_id
    row = get_policy_by_ref(tenant, policy_ref, store=store)
    assert row is not None
    assert row.index_status == "indexed"
