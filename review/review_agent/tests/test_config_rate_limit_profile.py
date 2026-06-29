"""Tests for P2 rate-limit profile settings."""

from review_agent.config import ReviewSettings, _apply_rate_limit_profile, get_settings


def test_mistral_conservative_profile_defaults(monkeypatch):
    monkeypatch.delenv("LLM_GLOBAL_CONCURRENCY", raising=False)
    monkeypatch.delenv("LLM_RATE_LIMIT_MAX_RETRIES", raising=False)
    monkeypatch.delenv("LLM_RATE_LIMIT_BACKOFF_MAX_SECONDS", raising=False)
    base = ReviewSettings(llm_rate_limit_profile="mistral_conservative")
    applied = _apply_rate_limit_profile(base)
    assert applied.llm_global_concurrency == 1
    assert applied.llm_rate_limit_max_retries == 5
    assert applied.llm_rate_limit_backoff_max_seconds == 60.0


def test_mistral_conservative_respects_explicit_env(monkeypatch):
    monkeypatch.setenv("LLM_GLOBAL_CONCURRENCY", "3")
    base = ReviewSettings(llm_rate_limit_profile="mistral_conservative")
    applied = _apply_rate_limit_profile(base)
    assert applied.llm_global_concurrency == 3


def test_get_settings_applies_profile(monkeypatch):
    monkeypatch.delenv("LLM_GLOBAL_CONCURRENCY", raising=False)
    monkeypatch.setenv("LLM_RATE_LIMIT_PROFILE", "mistral_conservative")
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.llm_rate_limit_profile == "mistral_conservative"
        assert settings.llm_global_concurrency == 1
    finally:
        get_settings.cache_clear()
