"""Minimal OpenAI-compatible structured JSON for ingest-time tagging."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from typing import TypeVar

import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def llm_api_key_available() -> bool:
    return bool(
        os.environ.get("SYNC_LLM_API_KEY")
        or os.environ.get("LLM_API_KEY")
        or os.environ.get("MISTRAL_API_KEY")
    )


def _api_key() -> str:
    key = (
        os.environ.get("SYNC_LLM_API_KEY")
        or os.environ.get("LLM_API_KEY")
        or os.environ.get("MISTRAL_API_KEY")
        or ""
    )
    if not key:
        raise RuntimeError(
            "SYNC_LLM_API_KEY, LLM_API_KEY, or MISTRAL_API_KEY is required for ingest LLM mode"
        )
    return key


def _base_url() -> str:
    return (os.environ.get("LLM_BASE_URL") or "https://api.mistral.ai/v1").rstrip("/")


def _rate_limit_settings() -> tuple[int, float, float]:
    max_retries = int(os.environ.get("LLM_RATE_LIMIT_MAX_RETRIES", "3"))
    base = float(os.environ.get("LLM_RATE_LIMIT_BACKOFF_BASE_SECONDS", "2.0"))
    max_delay = float(os.environ.get("LLM_RATE_LIMIT_BACKOFF_MAX_SECONDS", "30.0"))
    return max_retries, base, max_delay


def _is_rate_limit_status(status: int) -> bool:
    return status in (429, 503)


async def invoke_structured_json(
    *,
    model: str,
    system: str,
    user: str,
    schema: type[T],
    temperature: float = 0.0,
    timeout_seconds: float = 60.0,
    max_tokens: int = 2048,
) -> T:
    """Call chat completions and parse JSON into a Pydantic model."""
    payload = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
    }
    max_retries, base, max_delay = _rate_limit_settings()
    max_attempts = max(0, max_retries) + 1
    last_exc: BaseException | None = None

    for attempt in range(max_attempts):
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.post(
                    f"{_base_url()}/chat/completions",
                    headers={"Authorization": f"Bearer {_api_key()}"},
                    json=payload,
                )
                if _is_rate_limit_status(response.status_code):
                    raise httpx.HTTPStatusError(
                        f"rate limited ({response.status_code})",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                body = response.json()
            content = body["choices"][0]["message"]["content"]
            data = json.loads(content)
            return schema.model_validate(data)
        except httpx.HTTPStatusError as exc:
            if exc.response is None or not _is_rate_limit_status(exc.response.status_code):
                raise
            last_exc = exc
            if attempt >= max_attempts - 1:
                logger.warning(
                    "ingest LLM rate limit retries exhausted (%s attempts): %s",
                    max_attempts,
                    exc,
                )
                raise
            delay = min(base * (2**attempt), max_delay) + random.uniform(0, 0.5)
            logger.warning(
                "ingest LLM rate limited (attempt %s/%s), sleeping %.1fs",
                attempt + 1,
                max_attempts,
                delay,
            )
            await asyncio.sleep(delay)

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("invoke_structured_json retry loop exited without result")
