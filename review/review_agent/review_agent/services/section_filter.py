"""Filter contract sections for section-first review."""

from __future__ import annotations

from document_core.schemas.chunk import IndexedChunk


def filter_review_sections(
    sections: list[IndexedChunk],
    *,
    min_chars: int,
) -> list[IndexedChunk]:
    """Keep sections with enough text for meaningful review."""
    threshold = max(1, min_chars)
    return [s for s in sections if len((s.text or "").strip()) >= threshold]
