"""LLM access for compliance review (OpenAI-compatible / on-prem)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
from dataclasses import dataclass, field
from typing import Any, TypeVar

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from review_agent.errors import LLMUnavailableError
from review_agent.observability.metrics import record_llm_call
from review_agent.resilience.circuit_breaker import get_llm_breaker
from review_agent.resilience.failure_policy import (
    FailureClass,
    classify_llm_failure,
    gateway_max_attempts,
    get_current_review_posture,
    should_record_breaker_failure,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


@dataclass
class _ReviewLLMLimiter:
    semaphore: asyncio.Semaphore
    rate_limit_events: int = field(default=0)


_limiter: _ReviewLLMLimiter | None = None
_last_model_build: dict[str, Any] = {"temperature": 0.0, "max_tokens": None}


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def reset_llm_limiter() -> None:
    """Reset global limiter (tests and settings reload)."""
    global _limiter  # noqa: PLW0603
    _limiter = None
    from review_agent.models.llm_key_pool import reset_llm_key_pool

    reset_llm_key_pool()


def reset_limiter_rate_limit_events() -> None:
    """Zero review-scoped rate-limit counter without recreating the semaphore."""
    if _limiter is not None:
        _limiter.rate_limit_events = 0


def get_llm_limiter_stats() -> dict[str, int]:
    """Observability hook for review artifact ops (P1)."""
    from review_agent.models.llm_key_pool import get_key_pool_stats

    stats = get_key_pool_stats()
    if _limiter is None:
        return {"rate_limit_events": 0, **stats}
    return {"rate_limit_events": _limiter.rate_limit_events, **stats}


def _get_limiter() -> _ReviewLLMLimiter:
    global _limiter  # noqa: PLW0603
    if _limiter is None:
        from review_agent.config import get_settings

        cfg = get_settings()
        _limiter = _ReviewLLMLimiter(
            semaphore=asyncio.Semaphore(max(1, cfg.llm_global_concurrency))
        )
    return _limiter


def _is_rate_limit_error(exc: BaseException) -> bool:
    """Detect provider rate limits (Mistral 1300, HTTP 429, etc.)."""
    seen: set[int] = set()
    current: BaseException | None = exc
    depth = 0
    while current is not None and id(current) not in seen and depth < 4:
        seen.add(id(current))
        depth += 1
        try:
            import httpx

            if isinstance(current, httpx.HTTPStatusError):
                if current.response.status_code == 429:
                    return True
        except ImportError:
            pass
        text = str(current).lower()
        if (
            "429" in text
            or "rate limit" in text
            or "rate_limited" in text
            or '"code":"1300"' in text
            or "'code':'1300'" in text
            or '"code": "1300"' in text
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


def _rebuild_review_model() -> BaseChatModel:
    return get_review_model(
        temperature=float(_last_model_build.get("temperature", 0.0)),
        max_tokens=_last_model_build.get("max_tokens"),
    )


def get_review_model(*, temperature: float = 0.0, max_tokens: int | None = None) -> BaseChatModel:
    """Create a chat model using the same env vars as the research agent."""
    from langchain.chat_models import init_chat_model
    from review_agent.models.llm_key_pool import current_api_key, pool_active

    global _last_model_build  # noqa: PLW0603
    _last_model_build = {"temperature": temperature, "max_tokens": max_tokens}

    role = _env("COMPLIANCE_LLM_ROLE", "reasoning")
    model = _env(f"LLM_MODEL_{role.upper()}") or _env("LLM_MODEL") or "gpt-4o-mini"

    kwargs: dict[str, Any] = {"temperature": temperature}
    base_url = _env("LLM_BASE_URL")
    if pool_active():
        api_key = current_api_key()
    else:
        api_key = (
            _env("REVIEW_LLM_API_KEY")
            or _env("LLM_API_KEY")
            or _env("GROQ_API_KEY")
            or _env("GOOGLE_API_KEY")
            or _env("OPENAI_API_KEY")
            or _env("MISTRAL_API_KEY")
        )
    provider = _env("LLM_PROVIDER")

    if base_url:
        kwargs["base_url"] = base_url
    if api_key:
        kwargs["api_key"] = api_key
    if provider in ("google_genai", "google"):
        if api_key:
            os.environ.setdefault("GOOGLE_API_KEY", api_key)
        kwargs["model_provider"] = "google_genai"
    elif provider:
        kwargs["model_provider"] = "openai" if provider == "nvidia" and base_url else provider
    elif ":" not in model:
        kwargs["model_provider"] = "openai"

    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens

    return init_chat_model(model=model, **kwargs)


def _extract_json_payload(text: str) -> Any:
    """Parse JSON from model output; tolerate fences, arrays, and extra trailing objects."""
    stripped = text.strip()
    fence = re.search(r"```(?:json)?\s*([\[\{].*[\]\}])\s*```", stripped, re.DOTALL)
    if fence:
        stripped = fence.group(1).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError as exc:
        if "Extra data" not in str(exc):
            raise
        decoder = json.JSONDecoder()
        items: list[Any] = []
        idx = 0
        length = len(stripped)
        while idx < length:
            while idx < length and stripped[idx].isspace():
                idx += 1
            if idx >= length:
                break
            try:
                obj, end = decoder.raw_decode(stripped, idx)
            except json.JSONDecodeError:
                break
            items.append(obj)
            idx = end
        if not items:
            raise
        if len(items) == 1:
            return items[0]
        return {"items": items}


def _extract_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object from model output (legacy callers)."""
    payload = _extract_json_payload(text)
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        return {"items": payload}
    raise ValueError("expected JSON object")


async def _invoke_once(
    model: BaseChatModel,
    schema: type[T],
    *,
    system: str,
    user: str,
) -> T:
    """Single LLM attempt: structured output, then JSON parse fallback."""
    try:
        structured = model.with_structured_output(schema)
        result = await structured.ainvoke(
            [SystemMessage(content=system), HumanMessage(content=user)]
        )
        if isinstance(result, schema):
            return result
        return schema.model_validate(result)
    except Exception as exc:  # noqa: BLE001
        if _is_rate_limit_error(exc):
            raise
        logger.debug("structured output failed, falling back to JSON parse: %s", exc)

    try:
        response = await model.ainvoke(
            [SystemMessage(content=system), HumanMessage(content=user)]
        )
    except Exception as exc:  # noqa: BLE001
        if _is_rate_limit_error(exc):
            raise
        raise

    content = ""
    for body_attempt in range(2):
        if body_attempt > 0:
            response = await model.ainvoke(
                [SystemMessage(content=system), HumanMessage(content=user)]
            )
        content = getattr(response, "content", "")
        if not isinstance(content, str):
            raise ValueError("LLM returned non-text content")
        if content.strip():
            break
        if body_attempt == 0:
            logger.warning("LLM empty body — one structure retry")
            continue
        raise ValueError("LLM returned empty body")
    data = _extract_json_payload(content)
    return schema.model_validate(data)


async def invoke_structured(
    model: BaseChatModel,
    schema: type[T],
    *,
    system: str,
    user: str,
) -> T:
    """Invoke model with global concurrency cap and rate-limit retries."""
    from review_agent.config import get_settings

    breaker = get_llm_breaker()
    if not breaker.allow():
        record_llm_call("invoke_structured", "circuit_open")
        raise LLMUnavailableError("circuit_open:llm — LLM breaker is open")

    cfg = get_settings()
    limiter = _get_limiter()
    from review_agent.resilience.failure_policy import (
        FailureClass,
        gateway_max_attempts,
        get_current_review_posture,
        review_posture,
        should_record_breaker_failure,
    )

    if cfg.llm_hot_acquire_pause_enabled and cfg.llm_review_posture_enabled:
        pause_posture = get_current_review_posture()
        if pause_posture.value == "hot":
            events = max(limiter.rate_limit_events, 1)
            delay = min(
                cfg.llm_hot_acquire_pause_max_seconds,
                0.3 * events,
            ) + random.uniform(0, 0.2)
            await asyncio.sleep(delay)

    from review_agent.models.llm_key_pool import rotate_api_key_on_rate_limit

    async with limiter.semaphore:
        last_exc: BaseException | None = None
        max_attempts = max(0, cfg.llm_rate_limit_max_retries) + 1
        active_model = model
        for attempt in range(max_attempts):
            retried_with_new_key = False
            while True:
                try:
                    result = await _invoke_once(
                        active_model,
                        schema,
                        system=system,
                        user=user,
                    )
                    breaker.record_success()
                    record_llm_call("invoke_structured", "ok")
                    return result
                except Exception as exc:  # noqa: BLE001
                    if _is_rate_limit_error(exc):
                        last_exc = exc
                        limiter.rate_limit_events += 1
                        if not retried_with_new_key and rotate_api_key_on_rate_limit(cfg):
                            retried_with_new_key = True
                            active_model = _rebuild_review_model()
                            await asyncio.sleep(0.2 + random.uniform(0, 0.2))
                            continue
                        break
                    from review_agent.resilience.failure_policy import classify_llm_failure

                    if should_record_breaker_failure(classify_llm_failure(exc)):
                        breaker.record_failure()
                    record_llm_call("invoke_structured", "error")
                    raise

            exc = last_exc
            assert exc is not None
            current_posture = review_posture(
                {"llm_rate_limit_events": limiter.rate_limit_events},
                breaker.state,
            )
            allowed = gateway_max_attempts(
                FailureClass.QUOTA,
                current_posture,
                cfg.llm_rate_limit_max_retries,
                enabled=cfg.llm_review_posture_enabled,
            )
            if attempt >= allowed - 1:
                logger.warning(
                    "LLM rate limit retries exhausted (%s attempts): %s",
                    allowed,
                    exc,
                )
                record_llm_call("invoke_structured", "rate_limited")
                raise exc
            delay = min(
                cfg.llm_rate_limit_backoff_base_seconds * (2**attempt),
                cfg.llm_rate_limit_backoff_max_seconds,
            ) + random.uniform(0, 0.5)
            logger.warning(
                "LLM rate limited (attempt %s/%s), sleeping %.1fs",
                attempt + 1,
                allowed,
                delay,
            )
            await asyncio.sleep(delay)

        if last_exc is not None:
            raise last_exc
        raise RuntimeError("invoke_structured retry loop exited without result")
