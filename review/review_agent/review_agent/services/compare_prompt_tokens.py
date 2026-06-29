"""Prompt-faithful token estimates for section compare batching (Phase D)."""

from __future__ import annotations

from document_core.schemas.chunk import IndexedChunk, RetrievalHit
from document_core.schemas.taxonomy import normalize_categories
from review_agent.config import ReviewSettings, get_settings
from review_agent.schemas.obligation import ContractObligation
from review_agent.services.playbook_context import (
    PlaybookHints,
    format_playbook_hint_block,
    hints_from_chunk_metadata,
)
from review_agent.services.quote_validate import truncate_section


def estimate_tokens(text: str) -> int:
    """Conservative chars/4 token estimate."""
    return max(1, len(text) // 4)


def _hit_categories(hit: RetrievalHit) -> list[str]:
    raw = (hit.parent_chunk.metadata or {}).get("categories")
    if isinstance(raw, list):
        return normalize_categories([str(c) for c in raw])
    return []


def _playbook_compare_max_chars(settings: ReviewSettings) -> int | None:
    cap = settings.playbook_compare_max_chars
    return cap if cap > 0 else None


def estimate_compare_section_tokens(
    section: IndexedChunk,
    hits: list[RetrievalHit],
    *,
    settings: ReviewSettings | None = None,
    playbook_hints_by_document: dict[str, PlaybookHints] | None = None,
    categories: list[str] | None = None,
    extra_context: str = "",
) -> int:
    """Estimate tokens for one section block as built by section compare formatter."""
    cfg = settings or get_settings()
    if cfg.compare_token_budget_mode == "legacy":
        total = estimate_tokens(section.text or "")
        for hit in hits:
            total += estimate_tokens(hit.parent_chunk.text or "")
        return total + 30

    max_chars = cfg.section_compare_max_section_chars
    playbook_cap = _playbook_compare_max_chars(cfg)
    hints_map = playbook_hints_by_document or {}
    chars = 0
    chars += len(f"### Contract section: {section.section_id} — {section.title}")
    if categories:
        chars += len(f"- **Section categories:** {', '.join(categories)}") + 1
    extra = (extra_context or "").strip()
    if extra:
        chars += len(extra) + 1
    chars += len(truncate_section(section.text or "", max_chars)) + 12

    if not hits:
        chars += len("- **Policies:** [none retrieved]")
        return estimate_tokens("x" * chars)

    for idx, hit in enumerate(hits, start=1):
        parent = hit.parent_chunk
        ptext = truncate_section(parent.text or "", max_chars)
        chars += len(
            f"- **Policy {idx}** doc={parent.document_id} section={parent.section_id} "
            f"title={parent.title}"
        )
        hit_cats = _hit_categories(hit)
        if hit_cats:
            chars += len(f"- **Policy categories:** {', '.join(hit_cats)}") + 1
        if cfg.playbook_enrich_compare:
            hints = hints_map.get(str(parent.document_id))
            if hints is None:
                hints = hints_from_chunk_metadata(parent.metadata)
            hint_block = format_playbook_hint_block(
                hints,
                compare_max_chars=playbook_cap,
            )
            if hint_block:
                chars += len(hint_block) + 1
        chars += len(ptext) + 12

    return estimate_tokens("x" * max(chars, 1))


def estimate_compare_batch_tokens(
    sections: list[IndexedChunk],
    hits_by_section: dict[str, list[RetrievalHit]],
    *,
    settings: ReviewSettings | None = None,
    playbook_hints_by_document: dict[str, PlaybookHints] | None = None,
    categories_by_section: dict[str, list[str]] | None = None,
    extra_context_by_section: dict[str, str] | None = None,
) -> int:
    cfg = settings or get_settings()
    if cfg.compare_token_budget_mode == "legacy":
        total = 800
        for section in sections:
            total += estimate_tokens(section.text or "")
            for hit in hits_by_section.get(section.section_id, []):
                total += estimate_tokens(hit.parent_chunk.text or "")
        return total

    cats_map = categories_by_section or {}
    ctx_map = extra_context_by_section or {}
    total = 800
    for section in sections:
        total += estimate_compare_section_tokens(
            section,
            hits_by_section.get(section.section_id, []),
            settings=cfg,
            playbook_hints_by_document=playbook_hints_by_document,
            categories=cats_map.get(section.section_id),
            extra_context=ctx_map.get(section.section_id, ""),
        )
    return total


def estimate_obligation_tokens(
    obligation: ContractObligation,
    hits: list[RetrievalHit],
    *,
    settings: ReviewSettings | None = None,
) -> int:
    cfg = settings or get_settings()
    if cfg.compare_token_budget_mode == "legacy":
        total = estimate_tokens(obligation.text or "")
        for hit in hits:
            total += estimate_tokens(hit.parent_chunk.text or "")
        return total + 20

    max_chars = cfg.obligation_compare_max_obligation_chars
    chars = len(f"obligation_id={obligation.obligation_id}") + 12
    chars += len(truncate_section(obligation.text or "", max_chars)) + 12
    for hit in hits:
        parent = hit.parent_chunk
        ptext = truncate_section(parent.text or "", cfg.section_compare_max_section_chars)
        chars += len(f"policy section={parent.section_id}") + len(ptext) + 12
    return estimate_tokens("x" * max(chars, 1))


def estimate_obligation_batch_tokens(
    obligations: list[ContractObligation],
    hits_by_obligation: dict[str, list[RetrievalHit]],
    *,
    settings: ReviewSettings | None = None,
) -> int:
    cfg = settings or get_settings()
    if cfg.compare_token_budget_mode == "legacy":
        total = 800
        for obligation in obligations:
            total += estimate_tokens(obligation.text or "")
            for hit in hits_by_obligation.get(obligation.obligation_id, []):
                total += estimate_tokens(hit.parent_chunk.text or "")
        return total

    total = 800
    for obligation in obligations:
        total += estimate_obligation_tokens(
            obligation,
            hits_by_obligation.get(obligation.obligation_id, []),
            settings=cfg,
        )
    return total
