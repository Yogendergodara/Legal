"""Tests for section compare token batching."""

from __future__ import annotations

from uuid import uuid4

from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk, RetrievalHit
from review_agent.config import ReviewSettings
from review_agent.schemas.obligation import ContractObligation
from review_agent.services.token_budget import (
    compare_batch_split_stats,
    split_batch_by_token_budget,
    split_obligations_by_token_budget,
)


def _section(section_id: str) -> IndexedChunk:
    return IndexedChunk(
        chunk_id=f"c-{section_id}",
        document_id=uuid4(),
        tenant_id="t1",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id=section_id,
        section_path=section_id,
        title=section_id,
        text="Short contract clause text.",
    )


def test_batch_size_four_splits_eight_sections():
    sections = [_section(str(i)) for i in range(8)]
    bundles = {s.section_id: [] for s in sections}
    batches = split_batch_by_token_budget(
        sections,
        batch_size=4,
        max_tokens=48_000,
        bundles=bundles,
    )
    assert len(batches) == 2
    assert sum(len(batch) for batch in batches) == 8


def _obligation(obligation_id: str, *, text: str = "Short obligation text.") -> ContractObligation:
    return ContractObligation(
        obligation_id=obligation_id,
        section_id=obligation_id,
        text=text,
    )


def test_split_obligations_respects_token_budget():
    obligations = [
        _obligation("o1", text="x" * 40_000),
        _obligation("o2", text="y" * 40_000),
        _obligation("o3", text="z" * 40_000),
    ]
    settings = ReviewSettings(
        compare_token_budget_mode="aligned",
        obligation_compare_max_obligation_chars=2000,
    )
    batches = split_obligations_by_token_budget(
        obligations,
        batch_size=6,
        max_tokens=1500,
        hits_by_obligation={},
        settings=settings,
    )
    assert len(batches) == 3
    assert sum(len(batch) for batch in batches) == 3


def test_split_obligations_respects_batch_size_cap():
    obligations = [_obligation(f"o{i}") for i in range(8)]
    batches = split_obligations_by_token_budget(
        obligations,
        batch_size=4,
        max_tokens=48_000,
        hits_by_obligation={},
    )
    assert len(batches) == 2
    assert all(len(batch) <= 4 for batch in batches)
    assert sum(len(batch) for batch in batches) == 8


def test_best_fit_packs_small_sections_with_one_large():
    settings = ReviewSettings(
        compare_token_budget_mode="legacy",
        compare_token_pack_mode="best_fit",
        section_compare_batch_size=8,
    )
    large = _section("large")
    large = IndexedChunk(
        chunk_id="c-large",
        document_id=uuid4(),
        tenant_id="t1",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id="large",
        section_path="large",
        title="large",
        text="L" * 40_000,
    )
    smalls = [_section(f"s{i}") for i in range(7)]
    sections = [large] + smalls
    bundles = {s.section_id: [] for s in sections}
    first_fit = split_batch_by_token_budget(
        sections,
        batch_size=8,
        max_tokens=12_000,
        bundles=bundles,
        settings=ReviewSettings(
            compare_token_budget_mode="legacy",
            compare_token_pack_mode="first_fit",
        ),
    )
    best_fit = split_batch_by_token_budget(
        sections,
        batch_size=8,
        max_tokens=12_000,
        bundles=bundles,
        settings=settings,
    )
    assert len(best_fit) <= len(first_fit)
    assert sum(len(b) for b in best_fit) == 8


def test_compare_batch_split_stats_token_limited():
    stats = compare_batch_split_stats(8, [[], [], []], batch_size=4)
    assert stats["llm_batches_config_max"] == 2
    assert stats["llm_batches_token_limited"] == 1
