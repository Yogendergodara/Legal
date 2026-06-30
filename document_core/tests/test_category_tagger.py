"""Tests for per-parent policy category tagging (Phase 37C)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from document_core.config import DocumentCoreSettings, get_settings
from document_core.parser.text_parser import parse_text_to_tree
from document_core.schemas.category_tag import BatchSectionCategoryTagResult, SectionCategoryTag
from document_core.schemas.chunk import DocumentKind, IngestRequest, SectionNode
from document_core.services.category_tagger import (
    _prompt_template,
    _sanitize_llm_categories,
    broad_fallback_count,
    plan_llm_batches,
    reset_broad_fallback_count,
    tag_policy_sections,
)
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


def test_prompt_has_multi_topic_and_aup_rules():
    _prompt_template.cache_clear()
    prompt = _prompt_template()
    assert "Do not stop at the first pattern match" in prompt
    assert "Acceptable Use Policy" in prompt
    assert "access_control" in prompt


def test_sanitize_broad_only_fallback_to_keyword_specific():
    reset_broad_fallback_count()
    node = SectionNode(
        section_id="prohibited-1",
        section_path="prohibited-1",
        title="Prohibited Activities",
        level=1,
        text="Users must not distribute malware or file false DMCA claims.",
    )
    cats = _sanitize_llm_categories(["compliance", "security"], node=node)
    assert "access_control" in cats or "ip" in cats
    assert broad_fallback_count() == 1


def test_sanitize_broad_only_keeps_broad_when_keyword_also_broad():
    reset_broad_fallback_count()
    node = SectionNode(
        section_id="intro",
        section_path="intro",
        title="Introduction",
        level=1,
        text="This policy explains our services.",
    )
    cats = _sanitize_llm_categories(["compliance"], node=node)
    assert cats == ["compliance"]
    assert broad_fallback_count() == 0


def test_cap_via_tagger_keeps_up_to_five_specific_tags():
    from document_core.schemas.taxonomy import cap_section_categories

    capped = cap_section_categories(
        [
            "privacy",
            "compliance",
            "data_subject_rights",
            "data_retention",
            "liability",
            "indemnity",
            "limitation",
        ],
        max_tags=5,
    )
    assert len(capped) == 5
    assert "compliance" not in capped


def test_config_defaults_llm_tagger_and_five_tags():
    assert DocumentCoreSettings.model_fields["category_tagger_mode"].default == "llm"
    assert DocumentCoreSettings.model_fields["category_tagger_max_tags_per_section"].default == 5
    assert DocumentCoreSettings.model_fields["category_tagger_batch_size"].default == 10
    assert DocumentCoreSettings.model_fields["policy_profiler_mode"].default == "llm"


def _section_node(section_id: str, *, title: str, text: str) -> SectionNode:
    return SectionNode(
        section_id=section_id,
        section_path=section_id,
        title=title,
        level=1,
        text=text,
    )


def test_plan_llm_batches_whole_policy_when_small():
    nodes = [
        _section_node(f"s{i}", title=f"Sec {i}", text=f"body {i}" * 20)
        for i in range(7)
    ]
    settings = DocumentCoreSettings(
        category_tagger_batch_size=10,
        category_tagger_whole_policy_enabled=True,
        category_tagger_whole_policy_max_chars=32_000,
    )
    batches = plan_llm_batches(nodes, settings=settings)
    assert len(batches) == 1
    assert len(batches[0]) == 7


def test_plan_llm_batches_splits_large_policy():
    nodes = [
        _section_node(f"s{i}", title=f"Sec {i}", text="x" * 500)
        for i in range(25)
    ]
    settings = DocumentCoreSettings(
        category_tagger_batch_size=10,
        category_tagger_whole_policy_enabled=False,
        category_tagger_whole_policy_max_chars=32_000,
    )
    batches = plan_llm_batches(nodes, settings=settings)
    assert len(batches) == 3
    assert len(batches[0]) == 10
    assert len(batches[1]) == 10
    assert len(batches[2]) == 5


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
async def test_llm_whole_policy_single_call_mocked():
    tree = parse_text_to_tree(document_id=uuid4(), title="Policy", text=SAMPLE_POLICY)
    nodes = list(tree.sections)
    mock_result = BatchSectionCategoryTagResult(
        items=[
            SectionCategoryTag(section_id=nodes[0].section_id, categories=["liability", "limitation"]),
            SectionCategoryTag(section_id=nodes[1].section_id, categories=["indemnity", "liability"]),
        ]
    )
    settings = DocumentCoreSettings(category_tagger_mode="llm", category_tagger_max_tags_per_section=5)
    with patch(
        "document_core.services.category_tagger.invoke_structured_json",
        new_callable=AsyncMock,
        return_value=mock_result,
    ) as invoke_mock:
        tree, extra = await tag_policy_sections(tree, document_title="Policy", settings=settings)
    invoke_mock.assert_called_once()
    assert len(tree.sections[0].categories) <= 5
    assert extra["tagger"] == "llm"


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
