"""Semantic concept overlap for evidence gates (IPC-4 / E-EV1)."""

from __future__ import annotations

from functools import lru_cache

from document_core.schemas.chunk import RetrievalHit
from review_agent.config import ReviewSettings
from review_agent.schemas.obligation import ContractObligation
from review_agent.schemas.routing_plan import ObligationRoutingPlan


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b, strict=True))


@lru_cache(maxsize=256)
def _embed_cached(text: str, input_type: str) -> tuple[float, ...] | None:
    from document_core.embeddings.service import embed_documents, embed_query

    if input_type == "query":
        vec = embed_query(text)
    else:
        batch = embed_documents([text])
        vec = batch[0] if batch else None
    if not vec:
        return None
    return tuple(vec)


def semantic_concept_overlap(
    *,
    obligation: ContractObligation,
    plan: ObligationRoutingPlan,
    hits: list[RetrievalHit],
    settings: ReviewSettings | None = None,
    max_hit_chars: int = 1500,
) -> float:
    """Max cosine similarity between obligation+concepts and policy parent text."""
    _ = settings
    left_text = " ".join(
        part
        for part in (
            obligation.text or "",
            " ".join(plan.concepts or []),
            plan.intent or "",
        )
        if part
    ).strip()[:2000]
    if not left_text or not hits:
        return 0.0

    left_vec = _embed_cached(left_text, "query")
    if left_vec is None:
        return 0.0

    best = 0.0
    for hit in hits:
        parent = hit.parent_chunk
        right_text = f"{parent.title or ''} {parent.text or ''}".strip()[:max_hit_chars]
        if not right_text:
            continue
        right_vec = _embed_cached(right_text, "document")
        if right_vec is None:
            continue
        best = max(best, _cosine(list(left_vec), list(right_vec)))

    return round(best, 3)


def clear_semantic_overlap_cache() -> None:
    _embed_cached.cache_clear()
