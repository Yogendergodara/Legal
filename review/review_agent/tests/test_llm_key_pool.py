"""Tests for TEMP Mistral multi-key pool (llm_key_pool.py)."""

from __future__ import annotations

import asyncio

import pytest

from review_agent.config import get_settings
from review_agent.models import llm_gateway
from review_agent.models import llm_key_pool


class _DummyResult:
    value: str = "ok"


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_GLOBAL_CONCURRENCY", "2")
    monkeypatch.setenv("LLM_RATE_LIMIT_MAX_RETRIES", "3")
    llm_gateway.reset_llm_limiter()
    get_settings.cache_clear()
    yield
    llm_gateway.reset_llm_limiter()
    get_settings.cache_clear()


def test_pool_inactive_without_flag(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_KEY_POOL_ENABLED", "false")
    monkeypatch.setenv("LLM_API_KEYS", "key-a,key-b,key-c")
    get_settings.cache_clear()
    assert llm_key_pool.pool_active() is False
    assert llm_key_pool.current_api_key() == ""


def test_pool_active_with_three_keys(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_KEY_POOL_ENABLED", "true")
    monkeypatch.setenv("LLM_API_KEYS", "key-a,key-b,key-c")
    get_settings.cache_clear()
    assert llm_key_pool.pool_active() is True
    assert llm_key_pool.current_api_key() == "key-a"


def test_rotate_cycles_keys(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_KEY_POOL_ENABLED", "true")
    monkeypatch.setenv("LLM_API_KEYS", "key-a,key-b,key-c")
    get_settings.cache_clear()
    assert llm_key_pool.rotate_api_key_on_rate_limit() is True
    assert llm_key_pool.current_api_key() == "key-b"
    assert llm_key_pool.rotate_api_key_on_rate_limit() is True
    assert llm_key_pool.current_api_key() == "key-c"
    assert llm_key_pool.rotate_api_key_on_rate_limit() is True
    assert llm_key_pool.current_api_key() == "key-a"
    assert llm_key_pool.get_key_pool_stats()["key_pool_rotations"] == 3


@pytest.mark.asyncio
async def test_invoke_structured_rotates_before_backoff(monkeypatch: pytest.MonkeyPatch):
    from pydantic import BaseModel

    class _Result(BaseModel):
        value: str = "ok"

    monkeypatch.setenv("LLM_KEY_POOL_ENABLED", "true")
    monkeypatch.setenv("LLM_API_KEYS", "key-a,key-b,key-c")
    get_settings.cache_clear()

    sleeps: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(llm_gateway.asyncio, "sleep", _fake_sleep)

    built_keys: list[str] = []
    rate_exc = RuntimeError("HTTP 429 rate limit exceeded")
    invoke_count = 0

    class _FakeStructured:
        def __init__(self, parent: "_FakeModel") -> None:
            self._parent = parent

        async def ainvoke(self, _messages: object) -> _Result:
            nonlocal invoke_count
            invoke_count += 1
            if invoke_count == 1:
                raise rate_exc
            return _Result()

    class _FakeModel:
        def with_structured_output(self, _schema: type[BaseModel]) -> _FakeStructured:
            return _FakeStructured(self)

        async def ainvoke(self, _messages: object) -> object:
            raise AssertionError("should use structured path")

    def _tracking_get(**_kwargs: object) -> _FakeModel:
        built_keys.append(llm_key_pool.current_api_key())
        return _FakeModel()

    monkeypatch.setattr(llm_gateway, "get_review_model", _tracking_get)
    monkeypatch.setattr(llm_gateway, "_rebuild_review_model", _tracking_get)

    llm_gateway._last_model_build = {"temperature": 0.0, "max_tokens": None}

    result = await llm_gateway.invoke_structured(
        _FakeModel(),
        _Result,
        system="s",
        user="u",
    )
    assert result.value == "ok"
    assert llm_key_pool.get_key_pool_stats()["key_pool_rotations"] == 1
    assert llm_key_pool.current_api_key() == "key-b"
    # short rotate sleep only, no long backoff
    assert all(s < 1.0 for s in sleeps)
