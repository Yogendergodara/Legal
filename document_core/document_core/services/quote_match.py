"""Shared quote normalization for substring grounding (Phase E2)."""

from __future__ import annotations

import re

_LIST_MARKER_RE = re.compile(r"[\s]*[•●▪◦\-]\s*")


def normalize_for_quote_match(text: str) -> str:
    """Collapse bullets, whitespace, and case for tolerant substring checks."""
    cleaned = (text or "").replace("\u2022", "•")
    cleaned = _LIST_MARKER_RE.sub(" ", cleaned)
    return re.sub(r"\s+", " ", cleaned.strip().lower())


def quote_matches(quote: str, haystack: str) -> bool:
    """True when quote text appears in haystack after format normalization."""
    quote_norm = normalize_for_quote_match(quote)
    if not quote_norm:
        return False
    if quote_norm in normalize_for_quote_match(haystack):
        return True
    if (quote or "").strip() in (haystack or ""):
        return True
    return False
