"""Tests for per-parent policy category tagging (Phase 37C)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from document_core.config import DocumentCoreSettings, get_settings
from document_core.parser.text_parser import parse_text_to_tree
from document_core.schemas.category_tag import BatchSectionCategoryTagResult, SectionCategoryTag
from document_core.schemas.chunk import DocumentKind, IngestRequest
from document_core.services.category_tagger import tag_policy_sections
from document_core.services.ingest import ingest_document
from document_core.services.metadata_at_ingest import infer_section_categories_keyword
from document_core.store.pgvector_store import PgVectorDocumentStore
from tests.fixtures import SAMPLE_POLICY


@pytest.fixture
def keyword_settings(monkeypatch):
    monkeypatch.setenv("CATEGORY_TAGGER_MODE", "keyword")
    get_settings.cache_clear()
    yield DocumentCoreSettings(category_tagger_mode="keyword")
    get_settings.cache_clear()


def test_keyword_tags_liability_section():
    cats = infer_section_categories_keyword(
        title="Limitation of Liability",
        text="Vendor liability shall not exceed fees paid.",
    )
    assert "liability" in cats


def test_keyword_tags_confidentiality():
    cats = infer_section_categories_keyword(
        title="Confidential Information",
        text="Recipient must protect confidential information.",
    )
    assert "confidentiality" in cats


def test_keyword_tags_indemnity():
    cats = infer_section_categories_keyword(
        title="Indemnification",
        text="Vendor shall indemnify and hold harmless Customer.",
    )
    assert "indemnity" in cats


def test_keyword_slavery_not_sla():
    cats = infer_section_categories_keyword(
        title="Code of Conduct",
        text="We prohibit modern slavery and human trafficking in our supply chain.",
    )
    assert "sla" not in cats
    assert "modern_slavery" in cats or "human_rights" in cats


def test_keyword_brand_security_not_cyber_security():
    cats = infer_section_categories_keyword(
        title="Logo Guidelines",
        text="Partners must follow brand security requirements for logo placement.",
    )
    assert "security" not in cats
    assert "trademark" in cats or "ip" in cats


def test_cap_via_tagger_drops_broad_tags():
    from document_core.schemas.taxonomy import cap_section_categories

    capped = cap_section_categories(
        ["privacy", "compliance", "security", "data_subject_rights"],
        max_tags=3,
    )
    assert capped == ["privacy", "data_subject_rights"]


@pytest.mark.asyncio
async def test_tag_policy_sections_keyword(keyword_settings):
    tree = parse_text_to_tree(document_id=uuid4(), title="Policy", text=SAMPLE_POLICY)
    tree, extra = await tag_policy_sections(
        tree,
        document_title="Policy",
        settings=keyword_settings,
    )
    all_cats = [cat for node in tree.sections for cat in node.categories]
    assert "liability" in all_cats
    assert "indemnity" in all_cats
    assert extra["tagger"] == "keyword"
    assert extra["auto_tagged"] is True


@pytest.mark.asyncio
async def test_ingest_policy_per_parent_categories(store: PgVectorDocumentStore, keyword_settings):
    tenant = "tagger-per-parent"
    result = await ingest_document(
        IngestRequest(
            tenant_id=tenant,
            title="Playbook",
            kind=DocumentKind.POLICY,
            text=SAMPLE_POLICY,
        ),
        store=store,
    )
    parents = store.get_parents(tenant, result.document_id)
    parent_cats = {(p.title.lower(), tuple(p.metadata.get("categories", []))) for p in parents}
    assert any("liability" in cats for _, cats in parent_cats)
    assert any("indemnity" in cats for _, cats in parent_cats)


@pytest.mark.asyncio
async def test_document_union_categories(store: PgVectorDocumentStore, keyword_settings):
    tenant = "tagger-union"
    result = await ingest_document(
        IngestRequest(
            tenant_id=tenant,
            title="Playbook",
            kind=DocumentKind.POLICY,
            text=SAMPLE_POLICY,
        ),
        store=store,
    )
    assert result.document_id in store.list_document_ids_by_categories(tenant, ["liability"])
    assert result.document_id in store.list_document_ids_by_categories(tenant, ["indemnity"])


@pytest.mark.asyncio
async def test_llm_batch_mocked(keyword_settings):
    tree = parse_text_to_tree(document_id=uuid4(), title="Policy", text=SAMPLE_POLICY)
    nodes = list(tree.sections)
    mock_result = BatchSectionCategoryTagResult(
        items=[
            SectionCategoryTag(section_id=nodes[0].section_id, categories=["liability"]),
            SectionCategoryTag(section_id=nodes[1].section_id, categories=["indemnity"]),
        ]
    )
    settings = DocumentCoreSettings(category_tagger_mode="llm")
    with patch(
        "document_core.services.category_tagger.invoke_structured_json",
        new_callable=AsyncMock,
        return_value=mock_result,
    ):
        tree, extra = await tag_policy_sections(tree, document_title="Policy", settings=settings)
    assert tree.sections[0].categories == ["liability"]
    assert tree.sections[1].categories == ["indemnity"]
    assert extra["tagger"] == "llm"


@pytest.mark.asyncio
async def test_llm_fallback_to_keyword(keyword_settings):
    tree = parse_text_to_tree(document_id=uuid4(), title="Policy", text=SAMPLE_POLICY)
    settings = DocumentCoreSettings(category_tagger_mode="llm")
    with patch(
        "document_core.services.category_tagger.invoke_structured_json",
        new_callable=AsyncMock,
        side_effect=RuntimeError("llm down"),
    ):
        tree, extra = await tag_policy_sections(tree, document_title="Policy", settings=settings)
    assert all(node.categories for node in tree.sections)
    assert extra["tagger"] == "keyword"
