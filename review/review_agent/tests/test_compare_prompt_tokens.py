"""Tests for prompt-faithful compare token estimation (Phase D)."""

from __future__ import annotations

from uuid import uuid4

from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk, RetrievalHit
from review_agent.config import ReviewSettings
from review_agent.services.compare_prompt_tokens import (
    estimate_compare_batch_tokens,
    estimate_compare_section_tokens,
    estimate_obligation_batch_tokens,
)
from review_agent.services.playbook_context import PlaybookHints
from review_agent.schemas.obligation import ContractObligation


def _section(section_id: str, *, text: str) -> IndexedChunk:
    return IndexedChunk(
        chunk_id=f"c-{section_id}",
        document_id=uuid4(),
        tenant_id="t1",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id=section_id,
        section_path=section_id,
        title=section_id,
        text=text,
    )


def _hit(*, text: str, doc_id=None) -> RetrievalHit:
    parent = IndexedChunk(
        chunk_id="p1",
        document_id=doc_id or uuid4(),
        tenant_id="t1",
        kind=DocumentKind.POLICY,
        chunk_role=ChunkRole.PARENT,
        section_id="p-sec",
        section_path="p-sec",
        title="Policy",
        text=text,
    )
    return RetrievalHit(parent_chunk=parent, score=0.9)


def test_truncation_lowers_section_estimate():
    long_text = "x" * 50_000
    section = _section("1", text=long_text)
    settings = ReviewSettings(
        compare_token_budget_mode="aligned",
        section_compare_max_section_chars=32_000,
        playbook_enrich_compare=False,
    )
    legacy = ReviewSettings(compare_token_budget_mode="legacy")
    aligned = estimate_compare_section_tokens(section, [], settings=settings)
    legacy_est = estimate_compare_section_tokens(section, [], settings=legacy)
    assert aligned < legacy_est


def test_playbook_included_in_estimate_when_enriched():
    section = _section("1", text="Contract clause.")
    doc_id = uuid4()
    hit = _hit(text="Policy body.", doc_id=doc_id)
    hints = {
        str(doc_id): PlaybookHints(
            preferred_position="Preferred " + ("p" * 2000),
            review_guidance="Check fees.",
        )
    }
    settings_full = ReviewSettings(
        compare_token_budget_mode="aligned",
        playbook_enrich_compare=True,
        playbook_compare_max_chars=0,
    )
    settings_trim = ReviewSettings(
        compare_token_budget_mode="aligned",
        playbook_enrich_compare=True,
        playbook_compare_max_chars=1500,
    )
    full = estimate_compare_section_tokens(
        section,
        [hit],
        settings=settings_full,
        playbook_hints_by_document=hints,
    )
    trimmed = estimate_compare_section_tokens(
        section,
        [hit],
        settings=settings_trim,
        playbook_hints_by_document=hints,
    )
    assert full > trimmed


def test_obligation_estimate_uses_truncation_cap():
    obligation = ContractObligation(
        obligation_id="o1",
        section_id="1",
        text="z" * 10_000,
    )
    settings = ReviewSettings(
        compare_token_budget_mode="aligned",
        obligation_compare_max_obligation_chars=2000,
    )
    legacy = ReviewSettings(compare_token_budget_mode="legacy")
    aligned = estimate_obligation_batch_tokens([obligation], {}, settings=settings)
    legacy_est = estimate_obligation_batch_tokens([obligation], {}, settings=legacy)
    assert aligned < legacy_est


def test_batch_estimate_sums_sections():
    sections = [_section("1", text="a" * 1000), _section("2", text="b" * 1000)]
    hits = {s.section_id: [] for s in sections}
    settings = ReviewSettings(compare_token_budget_mode="aligned", playbook_enrich_compare=False)
    total = estimate_compare_batch_tokens(sections, hits, settings=settings)
    per_section = sum(
        estimate_compare_section_tokens(s, [], settings=settings) for s in sections
    )
    assert total == 800 + per_section
