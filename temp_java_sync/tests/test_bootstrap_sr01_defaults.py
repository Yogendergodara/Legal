"""Tests for SR-01 bootstrap retrieval defaults."""

from __future__ import annotations

import os

from bootstrap_env import apply_golden_review_defaults, apply_sr01_retrieval_defaults


def test_apply_sr01_retrieval_defaults(monkeypatch):
    monkeypatch.delenv("SR01_RETRIEVAL_OPT_OUT", raising=False)
    monkeypatch.delenv("RETRIEVAL_MEANING_FIRST_ENABLED", raising=False)
    monkeypatch.delenv("RETRIEVAL_CATEGORY_HARD_FILTER", raising=False)
    monkeypatch.delenv("COMPARE_HIT_ALLOW_PRIMARY_FALLBACK", raising=False)
    apply_sr01_retrieval_defaults()
    assert os.environ["RETRIEVAL_MEANING_FIRST_ENABLED"] == "true"
    assert os.environ["RETRIEVAL_CATEGORY_HARD_FILTER"] == "false"
    assert os.environ["COMPARE_HIT_ALLOW_PRIMARY_FALLBACK"] == "true"


def test_golden_review_defaults_includes_sr01(monkeypatch):
    monkeypatch.delenv("SR01_RETRIEVAL_OPT_OUT", raising=False)
    monkeypatch.delenv("RETRIEVAL_MEANING_FIRST_ENABLED", raising=False)
    apply_golden_review_defaults()
    assert os.environ.get("RETRIEVAL_MEANING_FIRST_ENABLED") == "true"
