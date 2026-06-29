"""Tests for RC-12 golden LLM profile defaults."""

from __future__ import annotations

import os

from bootstrap_env import apply_golden_llm_profile_defaults


def test_force_overrides_default_profile(monkeypatch):
    monkeypatch.setenv("LLM_RATE_LIMIT_PROFILE", "default")
    monkeypatch.setenv("GOLDEN_LLM_PROFILE_FORCE", "true")
    apply_golden_llm_profile_defaults()
    assert os.environ["LLM_RATE_LIMIT_PROFILE"] == "mistral_conservative"


def test_setdefault_when_unset(monkeypatch):
    monkeypatch.delenv("LLM_RATE_LIMIT_PROFILE", raising=False)
    monkeypatch.delenv("GOLDEN_LLM_PROFILE_FORCE", raising=False)
    apply_golden_llm_profile_defaults()
    assert os.environ["LLM_RATE_LIMIT_PROFILE"] == "mistral_conservative"


def test_opt_out_skips(monkeypatch):
    monkeypatch.setenv("LLM_RATE_LIMIT_PROFILE", "default")
    monkeypatch.setenv("GOLDEN_LLM_PROFILE_OPT_OUT", "true")
    apply_golden_llm_profile_defaults()
    assert os.environ["LLM_RATE_LIMIT_PROFILE"] == "default"
