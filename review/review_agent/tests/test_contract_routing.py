"""Tests for contract routing (Phase 6 Pass 1)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk

from review_agent.config import ReviewSettings
from review_agent.schemas.contract_routing import ContractRoutingResult
from review_agent.services.contract_routing import (
    build_routing_context,
    load_routing_topic_hints,
    route_contract,
    route_contract_lexical,
)
from tests.fixtures import SAMPLE_CONTRACT


def _section(title: str, text: str = "") -> IndexedChunk:
    return IndexedChunk(
        chunk_id="p1",
        document_id=uuid4(),
        tenant_id="demo",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id="1",
        section_path="1",
        title=title,
        text=text or title,
    )


def test_load_routing_topic_hints_includes_liability():
    hints = load_routing_topic_hints()
    assert any("liability" in h.lower() for h in hints)


def test_build_routing_context_from_sections():
    sections = [
        _section("12.2 Limitation of Liability", "liability cap text"),
        _section("13. Indemnification", "indemnify text"),
    ]
    context = build_routing_context(
        contract_text=SAMPLE_CONTRACT,
        contract_sections=sections,
        max_chars=5000,
    )
    assert "Limitation of Liability" in context
    assert "Indemnification" in context


def test_lexical_routing_extracts_liability_and_indemnity():
    sections = [
        _section("12.2 Limitation of Liability"),
        _section("13. Indemnification"),
    ]
    result = route_contract_lexical(
        contract_sections=sections,
        contract_text="",
        contract_type_hint="msa",
    )
    topics_lower = " ".join(result.topics).lower()
    assert "liability" in topics_lower
    assert "indemn" in topics_lower
    assert result.contract_type == "msa"


def test_contract_routing_result_dedupes_topics():
    result = ContractRoutingResult(
        topics=["Limitation of Liability", "limitation of liability", "Indemnification"],
        section_titles=["A"],
    )
    assert len(result.topics) == 2


@pytest.mark.asyncio
async def test_route_contract_lexical_mode_no_llm():
    settings = ReviewSettings(contract_routing_mode="lexical")
    result, warnings = await route_contract(
        contract_text=SAMPLE_CONTRACT,
        contract_sections=None,
        contract_type_hint=None,
        settings=settings,
    )
    assert result.topics
    assert not warnings


@pytest.mark.asyncio
async def test_route_contract_llm_fail_open(monkeypatch):
    async def _boom(*_a, **_k):
        raise RuntimeError("llm down")

    monkeypatch.setattr(
        "review_agent.services.contract_routing.invoke_structured",
        _boom,
    )
    settings = ReviewSettings(contract_routing_mode="llm")
    result, warnings = await route_contract(
        contract_text=SAMPLE_CONTRACT,
        contract_sections=[_section("13. Indemnification")],
        settings=settings,
    )
    assert result.topics
    assert any("lexical" in w.lower() for w in warnings)
