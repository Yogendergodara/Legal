"""Tests for hybrid batch utilities."""

from __future__ import annotations

from review_agent.schemas.review_category import ReviewCategory
from review_agent.services.compliance_batch import chunk_categories


def test_chunk_categories():
    categories = [
        ReviewCategory(category_id=f"c{i}", label=f"L{i}", source="policy_section")
        for i in range(7)
    ]
    batches = chunk_categories(categories, 3)
    assert len(batches) == 3
    assert len(batches[0]) == 3
    assert len(batches[-1]) == 1
