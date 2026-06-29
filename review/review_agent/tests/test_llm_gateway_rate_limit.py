"""Tests for global LLM concurrency and 429 backoff (Phase 21 P0)."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from review_agent.models import llm_gateway


class _DummyResult(BaseModel):
    value: str = "ok"


class _FakeStructured:
    def __init__(self, parent: "_FakeModel") -> None:
        self._parent = parent

    async def ainvoke(self, _messages: Any) -> _DummyResult:
        self._parent.structured_calls += 1
        if self._parent.structured_failures:
            exc = self._parent.structured_failures.pop(0)
            raise exc
        return _DummyResult()


class _FakeModel:
    def __init__(
        self,
        *,
        structured_failures: list[BaseException] | None = None,
        plain_failures: list[BaseException] | None = None,
        invoke_delay: float = 0.0,
    ) -> None:
        self.structured_failures = list(structured_failures or [])
        self.plain_failures = list(plain_failures or [])
        self.structured_calls = 0
        self.plain_calls = 0
        self.invoke_delay = invoke_delay
        self.concurrent = 0
        self.max_concurrent = 0

    def with_structured_output(self, _schema: type[BaseModel]) -> _FakeStructured:
        return _FakeStructured(self)

    async def ainvoke(self, _messages: Any) -> Any:
        self.plain_calls += 1
        self.concurrent += 1
        self.max_concurrent = max(self.max_concurrent, self.concurrent)
        try:
            if self.invoke_delay:
                await asyncio.sleep(self.invoke_delay)
            if self.plain_failures:
                raise self.plain_failures.pop(0)
            class _Resp:
                content = '{"value": "fallback"}'

            return _Resp()
        finally:
            self.concurrent -= 1


@pytest.fixture(autouse=True)
def _reset_limiter(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_GLOBAL_CONCURRENCY", "2")
    monkeypatch.setenv("LLM_RATE_LIMIT_MAX_RETRIES", "3")
    from review_agent.config import get_settings

    llm_gateway.reset_llm_limiter()
    get_settings.cache_clear()
    yield
    llm_gateway.reset_llm_limiter()
    get_settings.cache_clear()


def test_is_rate_limit_mistral_1300():
    exc = RuntimeError(
        'Error response 429 while fetching https://api.mistral.ai/v1/chat/completions: '
        '{"object":"error","message":"Rate limit exceeded","type":"rate_limited","code":"1300"}'
    )
    assert llm_gateway._is_rate_limit_error(exc) is True


def test_is_rate_limit_httpx_429():
    request = httpx.Request("POST", "https://api.example.com/v1/chat/completions")
    response = httpx.Response(429, request=request)
    assert llm_gateway._is_rate_limit_error(httpx.HTTPStatusError("429", request=request, response=response))


def test_is_rate_limit_not_validation():
    assert llm_gateway._is_rate_limit_error(ValidationError.from_exception_data("x", [])) is False


@pytest.mark.asyncio
async def test_retry_succeeds_after_rate_limit(monkeypatch: pytest.MonkeyPatch):
    sleeps: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(llm_gateway.asyncio, "sleep", _fake_sleep)

    rate_exc = RuntimeError("rate_limited code 1300")
    model = _FakeModel(structured_failures=[rate_exc, rate_exc])

    result = await llm_gateway.invoke_structured(
        model,  # type: ignore[arg-type]
        _DummyResult,
        system="s",
        user="u",
    )
    assert result.value == "ok"
    assert model.structured_calls == 3
    assert len(sleeps) == 2


@pytest.mark.asyncio
async def test_hot_posture_single_attempt_on_429(monkeypatch: pytest.MonkeyPatch):
    from review_agent.config import get_settings

    async def _noop_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(llm_gateway.asyncio, "sleep", _noop_sleep)
    monkeypatch.setenv("LLM_REVIEW_POSTURE_ENABLED", "true")

    llm_gateway.reset_llm_limiter()
    get_settings.cache_clear()
    limiter = llm_gateway._get_limiter()
    limiter.rate_limit_events = 3

    rate_exc = RuntimeError("HTTP 429 rate limit exceeded")
    model = _FakeModel(structured_failures=[rate_exc, rate_exc, rate_exc, rate_exc])

    with pytest.raises(RuntimeError, match="429"):
        await llm_gateway.invoke_structured(
            model,  # type: ignore[arg-type]
            _DummyResult,
            system="s",
            user="u",
        )
    assert model.structured_calls == 1
    assert llm_gateway.get_llm_limiter_stats()["rate_limit_events"] == 4


@pytest.mark.asyncio
async def test_quota_429_does_not_trip_breaker(monkeypatch: pytest.MonkeyPatch):
    from review_agent.config import get_settings
    from review_agent.resilience.circuit_breaker import get_llm_breaker, reset_all_breakers

    async def _noop_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(llm_gateway.asyncio, "sleep", _noop_sleep)
    reset_all_breakers()
    llm_gateway.reset_llm_limiter()
    get_settings.cache_clear()

    rate_exc = RuntimeError("HTTP 429 rate limit exceeded")
    model = _FakeModel(structured_failures=[rate_exc] * 100)

    for _ in range(20):
        with pytest.raises(RuntimeError, match="429"):
            await llm_gateway.invoke_structured(
                model,  # type: ignore[arg-type]
                _DummyResult,
                system="s",
                user="u",
            )

    assert get_llm_breaker().state == get_llm_breaker().CLOSED


@pytest.mark.asyncio
async def test_retry_exhausted_raises(monkeypatch: pytest.MonkeyPatch):
    async def _noop_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(llm_gateway.asyncio, "sleep", _noop_sleep)

    rate_exc = RuntimeError("HTTP 429 rate limit exceeded")
    model = _FakeModel(structured_failures=[rate_exc, rate_exc, rate_exc, rate_exc])

    with pytest.raises(RuntimeError, match="429"):
        await llm_gateway.invoke_structured(
            model,  # type: ignore[arg-type]
            _DummyResult,
            system="s",
            user="u",
        )
    # Dynamic posture (B-RC-F7): HOT after 3 events caps quota retries to 1 attempt
    assert model.structured_calls == 3
    assert llm_gateway.get_llm_limiter_stats()["rate_limit_events"] == 3


@pytest.mark.asyncio
async def test_non_rate_limit_no_retry():
    model = _FakeModel(
        structured_failures=[ValueError("bad schema")],
        plain_failures=[ValueError("bad json")],
    )

    with pytest.raises(ValueError, match="bad json"):
        await llm_gateway.invoke_structured(
            model,  # type: ignore[arg-type]
            _DummyResult,
            system="s",
            user="u",
        )
    assert model.structured_calls == 1
    assert model.plain_calls == 1
    assert llm_gateway.get_llm_limiter_stats()["rate_limit_events"] == 0


@pytest.mark.asyncio
async def test_global_semaphore_limits_concurrency(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_GLOBAL_CONCURRENCY", "2")
    from review_agent.config import get_settings

    llm_gateway.reset_llm_limiter()
    get_settings.cache_clear()

    model = _FakeModel(invoke_delay=0.05)
    # Force structured path to fail non-rate-limit so plain ainvoke runs (tracks concurrency)
    model.structured_failures = [ValueError("use fallback")] * 10

    async def _run():
        return await llm_gateway.invoke_structured(
            model,  # type: ignore[arg-type]
            _DummyResult,
            system="s",
            user="u",
        )

    await asyncio.gather(*[_run() for _ in range(6)])
    assert model.max_concurrent <= 2
