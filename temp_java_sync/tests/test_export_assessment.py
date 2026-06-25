"""Tests for assessment slug and export helpers (Phase O)."""

from __future__ import annotations

from export_assessment import assessment_slug


def test_assessment_slug_xecurify_and_acme() -> None:
    assert assessment_slug("Mutual NDA - Xecurify / Recipient") == "xecurify_nda"
    assert assessment_slug("Mutual NDA — Acme Corp / CloudVendor Inc.") == "acme_nda"
