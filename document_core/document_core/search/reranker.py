"""Rerank retrieval hits (no-op v1; cross-encoder optional later)."""

from __future__ import annotations

from document_core.schemas.chunk import RetrievalHit


def rerank_hits(
    query: str,
    hits: list[RetrievalHit],
    *,
    top_k: int,
    enabled: bool = False,
) -> list[RetrievalHit]:
    """Return top_k hits by score. Cross-encoder hook when enabled."""
    _ = query
    if enabled:
        # Phase 10 v1.1: plug cross-encoder here
        pass
    ordered = sorted(hits, key=lambda h: h.score, reverse=True)
    return ordered[: max(1, top_k)]
