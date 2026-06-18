"""Batching utilities for hybrid compliance."""

from __future__ import annotations

from collections.abc import Sequence

from review_agent.schemas.review_category import ReviewCategory


def chunk_categories(
    categories: Sequence[ReviewCategory],
    batch_size: int,
) -> list[list[ReviewCategory]]:
    size = max(1, batch_size)
    items = list(categories)
    return [items[i : i + size] for i in range(0, len(items), size)]
