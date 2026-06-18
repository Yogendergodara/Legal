"""LLM access for compliance review (OpenAI-compatible / on-prem)."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, TypeVar

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def get_review_model(*, temperature: float = 0.0, max_tokens: int | None = None) -> BaseChatModel:
    """Create a chat model using the same env vars as the research agent."""
    from langchain.chat_models import init_chat_model

    role = _env("COMPLIANCE_LLM_ROLE", "reasoning")
    model = _env(f"LLM_MODEL_{role.upper()}") or _env("LLM_MODEL") or "gpt-4o-mini"

    kwargs: dict[str, Any] = {"temperature": temperature}
    base_url = _env("LLM_BASE_URL")
    api_key = _env("LLM_API_KEY") or _env("OPENAI_API_KEY") or _env("MISTRAL_API_KEY")
    provider = _env("LLM_PROVIDER")

    if base_url:
        kwargs["base_url"] = base_url
    if api_key:
        kwargs["api_key"] = api_key
    if provider:
        kwargs["model_provider"] = "openai" if provider == "nvidia" and base_url else provider
    elif ":" not in model:
        kwargs["model_provider"] = "openai"

    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens

    return init_chat_model(model=model, **kwargs)


def _extract_json_object(text: str) -> dict[str, Any]:
    """Parse JSON from model output, tolerating fenced code blocks."""
    stripped = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", stripped, re.DOTALL)
    if fence:
        stripped = fence.group(1)
    return json.loads(stripped)


async def invoke_structured(
    model: BaseChatModel,
    schema: type[T],
    *,
    system: str,
    user: str,
) -> T:
    """Invoke model with structured output; fall back to JSON parse + validate."""
    try:
        structured = model.with_structured_output(schema)
        result = await structured.ainvoke(
            [SystemMessage(content=system), HumanMessage(content=user)]
        )
        if isinstance(result, schema):
            return result
        return schema.model_validate(result)
    except Exception as exc:  # noqa: BLE001
        logger.debug("structured output failed, falling back to JSON parse: %s", exc)

    response = await model.ainvoke(
        [SystemMessage(content=system), HumanMessage(content=user)]
    )
    content = getattr(response, "content", "")
    if not isinstance(content, str):
        raise ValueError("LLM returned non-text content")
    data = _extract_json_object(content)
    return schema.model_validate(data)
