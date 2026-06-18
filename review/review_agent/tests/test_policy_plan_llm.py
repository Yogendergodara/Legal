"""Tests for optional LLM policy plan category filter (Phase 3)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk

from review_agent.config import ReviewSettings
from review_agent.schemas.policy_plan_llm import PolicyPlanFilterResult
from review_agent.schemas.review_category import ReviewCategory
from review_agent.services import policy_plan_llm
from review_agent.services.policy_plan_llm import _apply_filter_result, filter_categories_llm


def _category(category_id: str, label: str) -> ReviewCategory:
    doc_id = uuid4()
    return ReviewCategory(
        category_id=category_id,
        label=label,
        policy_document_id=doc_id,
        policy_section_id="s1",
        search_queries=[label],
    )


def _contract_section(title: str) -> IndexedChunk:
    return IndexedChunk(
        chunk_id="c1",
        document_id=uuid4(),
        tenant_id="demo",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id="1",
        section_path="1",
        title=title,
        text=title,
    )


@pytest.mark.asyncio
async def test_filter_disabled_returns_all(monkeypatch):
    called = False

    async def _fake_invoke(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("LLM should not be called")

    monkeypatch.setattr(policy_plan_llm, "invoke_structured", _fake_invoke)
    categories = [_category("a", "Liability"), _category("b", "Indemnity")]
    settings = ReviewSettings(review_plan_llm_filter=False)

    result = await filter_categories_llm(
        categories=categories,
        contract_sections=[_contract_section("Liability")],
        contract_type="msa",
        policy_titles_by_doc={},
        settings=settings,
    )

    assert result == categories
    assert called is False


@pytest.mark.asyncio
async def test_below_min_threshold_skips_llm(monkeypatch):
    called = False

    async def _fake_invoke(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("LLM should not be called")

    monkeypatch.setattr(policy_plan_llm, "invoke_structured", _fake_invoke)
    categories = [_category("a", "Liability"), _category("b", "Indemnity")]
    settings = ReviewSettings(
        review_plan_llm_filter=True,
        review_plan_llm_filter_min_categories=15,
    )

    result = await filter_categories_llm(
        categories=categories,
        contract_sections=[],
        contract_type=None,
        policy_titles_by_doc={},
        settings=settings,
    )

    assert len(result) == 2
    assert called is False


@pytest.mark.asyncio
async def test_mock_llm_returns_subset(monkeypatch):
    categories = [
        _category("a", "Liability"),
        _category("b", "Indemnity"),
        _category("c", "Termination"),
    ]

    async def _fake_invoke(_model, _schema, *, system, user):
        assert "relevant" in system.lower() or "closed list" in system.lower()
        assert "Liability" in user
        return PolicyPlanFilterResult(relevant_category_ids=["a", "c"])

    monkeypatch.setattr(policy_plan_llm, "get_review_model", lambda **_: object())
    monkeypatch.setattr(policy_plan_llm, "invoke_structured", _fake_invoke)

    settings = ReviewSettings(
        review_plan_llm_filter=True,
        review_plan_llm_filter_min_categories=2,
    )
    result = await filter_categories_llm(
        categories=categories,
        contract_sections=[_contract_section("Limitation of Liability")],
        contract_type="msa",
        policy_titles_by_doc={str(categories[0].policy_document_id): "Vendor Policy"},
        settings=settings,
    )

    assert [c.category_id for c in result] == ["a", "c"]


@pytest.mark.asyncio
async def test_unknown_id_stripped(monkeypatch):
    categories = [_category("a", "Liability"), _category("b", "Indemnity")]

    async def _fake_invoke(*_args, **_kwargs):
        return PolicyPlanFilterResult(relevant_category_ids=["a", "unknown-id"])

    monkeypatch.setattr(policy_plan_llm, "get_review_model", lambda **_: object())
    monkeypatch.setattr(policy_plan_llm, "invoke_structured", _fake_invoke)

    settings = ReviewSettings(
        review_plan_llm_filter=True,
        review_plan_llm_filter_min_categories=1,
    )
    result = await filter_categories_llm(
        categories=categories,
        contract_sections=[],
        contract_type=None,
        policy_titles_by_doc={},
        settings=settings,
    )

    assert [c.category_id for c in result] == ["a"]


def test_empty_filter_fail_open():
    categories = [_category("a", "Liability"), _category("b", "Indemnity")]
    result = _apply_filter_result(
        categories,
        PolicyPlanFilterResult(relevant_category_ids=[]),
    )
    assert result == categories


@pytest.mark.asyncio
async def test_llm_failure_fail_open(monkeypatch):
    categories = [_category("a", "Liability"), _category("b", "Indemnity")]

    async def _fake_invoke(*_args, **_kwargs):
        raise ValueError("model unavailable")

    monkeypatch.setattr(policy_plan_llm, "get_review_model", lambda **_: object())
    monkeypatch.setattr(policy_plan_llm, "invoke_structured", _fake_invoke)

    settings = ReviewSettings(
        review_plan_llm_filter=True,
        review_plan_llm_filter_min_categories=1,
        review_plan_llm_max_retries=0,
    )
    result = await filter_categories_llm(
        categories=categories,
        contract_sections=[],
        contract_type=None,
        policy_titles_by_doc={},
        settings=settings,
    )

    assert result == categories


@pytest.mark.asyncio
async def test_query_override_applied(monkeypatch):
    categories = [_category("a", "Liability"), _category("b", "Indemnity")]

    async def _fake_invoke(*_args, **_kwargs):
        return PolicyPlanFilterResult(
            relevant_category_ids=["a"],
            search_query_overrides={"a": ["limitation of liability cap"]},
        )

    monkeypatch.setattr(policy_plan_llm, "get_review_model", lambda **_: object())
    monkeypatch.setattr(policy_plan_llm, "invoke_structured", _fake_invoke)

    settings = ReviewSettings(
        review_plan_llm_filter=True,
        review_plan_llm_filter_min_categories=1,
    )
    result = await filter_categories_llm(
        categories=categories,
        contract_sections=[],
        contract_type=None,
        policy_titles_by_doc={},
        settings=settings,
    )

    assert len(result) == 1
    assert result[0].search_queries == ["limitation of liability cap"]
