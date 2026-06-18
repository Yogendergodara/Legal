"""Rough token budgeting for section compare batches."""

from __future__ import annotations

from document_core.schemas.chunk import IndexedChunk, RetrievalHit


def estimate_tokens(text: str) -> int:
    """Conservative chars/4 token estimate."""
    return max(1, len(text) // 4)


def estimate_section_batch_tokens(
    sections: list[IndexedChunk],
    bundles: dict[str, list[RetrievalHit]],
) -> int:
    total = 800  # prompt overhead
    for section in sections:
        total += estimate_tokens(section.text or "")
        for hit in bundles.get(section.section_id, []):
            total += estimate_tokens(hit.parent_chunk.text or "")
    return total


def split_batch_by_token_budget(
    sections: list[IndexedChunk],
    *,
    batch_size: int,
    max_tokens: int,
    bundles: dict[str, list[RetrievalHit]],
) -> list[list[IndexedChunk]]:
    """Group sections into batches respecting size and token budget."""
    if not sections:
        return []
    batches: list[list[IndexedChunk]] = []
    current: list[IndexedChunk] = []
    for section in sections:
        trial = current + [section]
        if len(trial) > batch_size:
            if current:
                batches.append(current)
            current = [section]
        elif estimate_section_batch_tokens(trial, bundles) > max_tokens and current:
            batches.append(current)
            current = [section]
        else:
            current = trial
    if current:
        batches.append(current)
    return batches
