"""Tests for shared quote normalization (Phase E2)."""

from __future__ import annotations

from document_core.services.quote_match import normalize_for_quote_match, quote_matches


def test_quote_matches_bullet_prefix() -> None:
    assert quote_matches(
        "• Support and respect internationally proclaimed human rights",
        "Support and respect internationally proclaimed human rights",
    )


def test_quote_matches_substring_without_bullet() -> None:
    haystack = "Hold all Confidential Information in strict confidence and protect it."
    assert quote_matches("• Hold all Confidential Information", haystack)


def test_normalize_strips_bullets() -> None:
    assert "support and respect" in normalize_for_quote_match("• Support and respect")
