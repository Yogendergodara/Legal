"""Tests for policy category taxonomy."""

from document_core.schemas.taxonomy import normalize_categories


def test_normalize_categories_dedupes_and_lowercases():
    assert normalize_categories(["Liability", " liability ", "Privacy"]) == [
        "liability",
        "privacy",
    ]


def test_normalize_empty():
    assert normalize_categories([]) == []
    assert normalize_categories(None) == []
