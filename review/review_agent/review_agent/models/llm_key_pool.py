"""TEMP — Mistral multi-key rotation on HTTP 429.

Enable with LLM_KEY_POOL_ENABLED=true and LLM_API_KEYS=key1,key2,key3.
Delete this module and revert llm_gateway hooks when a permanent quota strategy lands.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from review_agent.config import ReviewSettings

logger = logging.getLogger(__name__)

_pool_index: int = 0
_pool_rotations: int = 0


def reset_llm_key_pool() -> None:
    """Reset rotation state (tests and per-review scope)."""
    global _pool_index, _pool_rotations  # noqa: PLW0603
    _pool_index = 0
    _pool_rotations = 0


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _single_api_key() -> str:
    return (
        _env("REVIEW_LLM_API_KEY")
        or _env("LLM_API_KEY")
        or _env("OPENAI_API_KEY")
        or _env("MISTRAL_API_KEY")
    )


def parse_api_keys(settings: ReviewSettings | None = None) -> list[str]:
    """Return ordered API keys from LLM_API_KEYS or a single legacy key."""
    if settings is None:
        from review_agent.config import get_settings

        settings = get_settings()

    raw = (settings.llm_api_keys or "").strip()
    if not raw:
        raw = _env("LLM_API_KEYS") or _env("MISTRAL_API_KEYS")
    keys = [part.strip() for part in raw.split(",") if part.strip()]
    if len(keys) >= 2:
        return keys
    single = _single_api_key()
    return [single] if single else []


def pool_active(settings: ReviewSettings | None = None) -> bool:
    if settings is None:
        from review_agent.config import get_settings

        settings = get_settings()
    if not settings.llm_key_pool_enabled:
        return False
    return len(parse_api_keys(settings)) >= 2


def current_api_key(settings: ReviewSettings | None = None) -> str:
    if not pool_active(settings):
        return _single_api_key()
    keys = parse_api_keys(settings)
    if not keys:
        return ""
    return keys[_pool_index % len(keys)]


def rotate_api_key_on_rate_limit(settings: ReviewSettings | None = None) -> bool:
    """Advance to the next key after a 429. Returns False when pool is off or single-key."""
    global _pool_index, _pool_rotations  # noqa: PLW0603
    if settings is None:
        from review_agent.config import get_settings

        settings = get_settings()
    if not pool_active(settings):
        return False
    keys = parse_api_keys(settings)
    if len(keys) < 2:
        return False
    _pool_index = (_pool_index + 1) % len(keys)
    _pool_rotations += 1
    logger.warning(
        "LLM key pool rotated after 429 (slot %s/%s, key %s)",
        _pool_index + 1,
        len(keys),
        _mask_key(keys[_pool_index]),
    )
    return True


def get_key_pool_stats() -> dict[str, int]:
    return {
        "key_pool_rotations": _pool_rotations,
        "key_pool_index": _pool_index,
    }


def _mask_key(key: str) -> str:
    if len(key) <= 8:
        return "***"
    return f"…{key[-4:]}"
