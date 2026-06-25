"""Tests for policy catalog profiler (Phase R0)."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

from document_core.config import DocumentCoreSettings
from document_core.parser.structured_sections import sections_to_tree
from document_core.schemas.chunk import DocumentKind, IngestRequest, IngestSectionInput
from document_core.schemas.policy_catalog import PolicyProfilerLLMResult
from document_core.services.ingest import ingest_document
from document_core.services.policy_profiler import profile_policy_tree


@pytest.mark.asyncio
async def test_policy_profiler_keyword_fallback(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    settings = DocumentCoreSettings(policy_profiler_mode="keyword")
    tree = sections_to_tree(
        document_id=uuid4(),
        title="Incident Response Plan",
        sections=[
            IngestSectionInput(
                section_id="1",
                title="Notification",
                text="Customers will be notified within 8 hours of a security breach.",
            )
        ],
    )
    profile, meta = await profile_policy_tree(
        tree,
        document_title="Incident Response Plan",
        settings=settings,
    )
    assert meta["profiler"] == "keyword"
    assert profile.topics
    assert "Incident Response Plan" in profile.aliases
    assert profile.profile_text


@pytest.mark.asyncio
async def test_policy_profiler_llm_mock(monkeypatch):
    async def _mock(**_kwargs):
        return PolicyProfilerLLMResult(
            summary="Handles security incidents and breach notification.",
            topics=["incident", "breach", "notification"],
            keywords=["8 hours", "ISMS"],
            aliases=["IR Plan"],
            obligation_types=["incident_notification"],
        )

    monkeypatch.setattr(
        "document_core.services.policy_profiler.invoke_structured_json",
        _mock,
    )
    settings = DocumentCoreSettings(policy_profiler_mode="llm")
    tree = sections_to_tree(
        document_id=uuid4(),
        title="Incident Response Plan",
        sections=[
            IngestSectionInput(section_id="1", title="Scope", text="Incident handling procedures."),
        ],
    )
    profile, meta = await profile_policy_tree(
        tree,
        document_title="Incident Response Plan",
        settings=settings,
    )
    assert meta["profiler"] == "llm"
    assert "incident" in profile.topics
    assert profile.summary


@pytest.mark.integration
@pytest.mark.asyncio
async def test_catalog_vector_upsert_after_index_policy(store):
    os.environ["POLICY_PROFILER_MODE"] = "keyword"
    from document_core.config import get_settings

    get_settings.cache_clear()
    doc_id = uuid4()
    request = IngestRequest(
        tenant_id="r0-catalog",
        document_id=doc_id,
        title="Incident Response Plan",
        kind=DocumentKind.POLICY,
        text="Notify customers within 8 hours after a security breach.",
        metadata={"policy_ref": "incident-response"},
    )
    await ingest_document(request, store=store)
    from sqlalchemy import text

    row = store.engine.connect().execute(
        text(
            """
            SELECT profile_text FROM policy_catalog_vectors
            WHERE tenant_id = :tenant_id AND document_id = :document_id
            """
        ),
        {"tenant_id": "r0-catalog", "document_id": doc_id},
    ).scalar()
    assert row
    assert "Incident" in row
