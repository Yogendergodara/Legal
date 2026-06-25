"""Tests for policy catalog search (Phase R0)."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

from document_core.schemas.chunk import DocumentKind, IngestRequest
from document_core.schemas.policy_catalog import CatalogSearchRequest
from document_core.services.catalog_search import search_policy_catalog
from document_core.services.ingest import ingest_document


@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_policy_catalog_incident(store):
    os.environ["POLICY_PROFILER_MODE"] = "keyword"
    os.environ["SEARCH_BACKEND"] = "lexical"
    from document_core.config import get_settings

    get_settings.cache_clear()
    tenant = "r0-search"
    incident_id = uuid4()
    privacy_id = uuid4()
    await ingest_document(
        IngestRequest(
            tenant_id=tenant,
            document_id=incident_id,
            title="Incident Response Plan",
            kind=DocumentKind.POLICY,
            text="Security incident breach notification within 8 hours to customers.",
            metadata={"policy_ref": "incident-response"},
        ),
        store=store,
    )
    await ingest_document(
        IngestRequest(
            tenant_id=tenant,
            document_id=privacy_id,
            title="Privacy Policy",
            kind=DocumentKind.POLICY,
            text="Personal data processing and privacy rights for subscribers.",
            metadata={"policy_ref": "privacy"},
        ),
        store=store,
    )
    hits = await search_policy_catalog(
        CatalogSearchRequest(
            tenant_id=tenant,
            query="breach notification",
            top_k=5,
        ),
        store=store,
    )
    assert hits
    assert hits[0].document_id == incident_id
    assert "Incident" in hits[0].title


@pytest.mark.integration
@pytest.mark.asyncio
async def test_skip_reindex_still_updates_profile(store, monkeypatch):
    monkeypatch.setenv("POLICY_PROFILER_MODE", "keyword")
    from document_core.config import get_settings

    get_settings.cache_clear()
    tenant = "r0-skip-profile"
    doc_id = uuid4()
    request = IngestRequest(
        tenant_id=tenant,
        document_id=doc_id,
        title="Data Retention Policy",
        kind=DocumentKind.POLICY,
        text="Retention schedules and secure deletion requirements.",
        metadata={"policy_ref": "data-retention"},
    )
    await ingest_document(request, store=store)
    get_settings.cache_clear()
    monkeypatch.setenv("POLICY_PROFILER_ENABLED", "false")
    request.metadata = {
        "policy_ref": "data-retention",
        "catalog_profile": {
            "summary": "Updated retention summary for catalog search.",
            "topics": ["retention", "deletion"],
            "keywords": ["secure deletion"],
            "aliases": ["Data Retention Policy"],
            "obligation_types": ["data_retention"],
            "profile_text": "Data Retention Policy. Updated retention summary secure deletion.",
            "catalog_version": 2,
            "profiler": "keyword",
            "profiled_at": "2026-06-25T00:00:00Z",
        },
        "profiler": "keyword",
    }
    await ingest_document(request, store=store)
    hits = await search_policy_catalog(
        CatalogSearchRequest(tenant_id=tenant, query="secure deletion", top_k=3),
        store=store,
    )
    assert hits
    assert hits[0].document_id == doc_id
