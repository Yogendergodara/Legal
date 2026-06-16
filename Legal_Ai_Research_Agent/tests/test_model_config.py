"""Tests for context-aware completion token capping."""

import importlib

import deep_research_from_scratch.model_config as model_config
from deep_research_from_scratch.model_config import (
    cap_max_tokens_for_prompt,
    estimate_token_count,
    resolve_max_tokens,
)


def test_estimate_token_count_is_positive_for_nonempty_text():
    assert estimate_token_count("hello world") > 0


def test_resolve_max_tokens_honors_global_cap(monkeypatch):
    monkeypatch.setenv("LLM_MAX_TOKENS", "4096")
    monkeypatch.delenv("LLM_MAX_TOKENS_WRITER", raising=False)
    assert resolve_max_tokens("writer", explicit_max_tokens=32000) == 4096


def test_cap_max_tokens_for_prompt_without_context_length(monkeypatch):
    monkeypatch.delenv("LLM_CONTEXT_LENGTH", raising=False)
    assert cap_max_tokens_for_prompt("x" * 1000, requested_max_tokens=4096) is None


def test_cap_max_tokens_for_prompt_shrinks_for_large_prompt(monkeypatch):
    monkeypatch.setenv("LLM_CONTEXT_LENGTH", "10000")
    monkeypatch.setenv("LLM_MAX_TOKENS", "4096")
    monkeypatch.setenv("LLM_COMPLETION_BUFFER", "128")

    # Simulate the failing case: ~6310 input tokens, 4096 requested completion.
    prompt = "tok " * 6310
    capped = cap_max_tokens_for_prompt(prompt, role="writer", requested_max_tokens=32000)

    assert capped is not None
    assert capped < 4096
    assert capped <= 10000 - estimate_token_count(prompt) - 128


def test_fit_writer_prompt_shrinks_oversized_findings(monkeypatch):
    monkeypatch.setenv("LLM_CONTEXT_LENGTH", "10000")
    monkeypatch.setenv("LLM_MAX_TOKENS_WRITER", "4096")
    monkeypatch.setenv("LLM_MIN_WRITER_COMPLETION_TOKENS", "2048")
    monkeypatch.setenv("LLM_COMPLETION_BUFFER", "128")
    importlib.reload(model_config)

    findings = "fact " * 8000
    template = "HEADER\n<Findings>\n{findings}\n</Findings>\nFOOTER"
    prompt = template.format(findings=findings)

    def trim(text: str, budget: int) -> str:
        return text[:budget]

    fitted, capped = model_config.fit_writer_prompt(
        prompt,
        findings=findings,
        trim_findings=trim,
        requested_max_tokens=4096,
    )
    assert capped is not None
    assert capped >= 2048
    assert len(fitted) < len(prompt)


def test_cap_max_tokens_for_prompt_reload_after_env_change(monkeypatch):
    monkeypatch.setenv("LLM_CONTEXT_LENGTH", "10000")
    monkeypatch.setenv("LLM_MAX_TOKENS", "4096")
    importlib.reload(model_config)

    prompt = "tok " * 6310
    capped = model_config.cap_max_tokens_for_prompt(
        prompt, role="writer", requested_max_tokens=32000
    )
    assert capped is not None
    assert capped < 4096

    monkeypatch.delenv("LLM_CONTEXT_LENGTH", raising=False)
    importlib.reload(model_config)


def test_get_chat_model_passes_llm_api_key(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "mistral-small-latest")
    monkeypatch.setenv("LLM_PROVIDER", "mistralai")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)

    captured: dict = {}

    def fake_init_chat_model(model, **kwargs):
        captured["model"] = model
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(model_config, "init_chat_model", fake_init_chat_model)
    model_config.get_chat_model("reasoning")

    assert captured.get("model") == "mistral-small-latest"
    assert captured.get("api_key") == "test-key"
    assert captured.get("model_provider") == "mistralai"


def test_get_chat_model_strips_mistral_prefix(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "mistral:mistral-small-latest")
    monkeypatch.setenv("LLM_PROVIDER", "mistralai")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)

    captured: dict = {}

    def fake_init_chat_model(model, **kwargs):
        captured["model"] = model
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(model_config, "init_chat_model", fake_init_chat_model)
    model_config.get_chat_model("reasoning")

    assert captured.get("model") == "mistral-small-latest"
    assert captured.get("model_provider") == "mistralai"
