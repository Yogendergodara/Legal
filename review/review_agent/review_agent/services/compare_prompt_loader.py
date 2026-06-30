"""Obligation compare prompt version selection (IPC0-R)."""

from __future__ import annotations

from pathlib import Path

from review_agent.config import ReviewSettings, get_settings

_PROMPTS = Path(__file__).resolve().parent.parent / "prompts"


def obligation_compare_prompt_path(settings: ReviewSettings | None = None) -> Path:
    """Return v1 or v2 prompt file; never depends on mutable obligation_compare.md alone."""
    cfg = settings or get_settings()
    if cfg.obligation_compare_prompt_v2_enabled:
        path = _PROMPTS / "obligation_compare_v2.md"
    else:
        path = _PROMPTS / "obligation_compare_v1.md"
    if path.is_file():
        return path
    return _PROMPTS / "obligation_compare.md"


def active_obligation_compare_prompt_label(settings: ReviewSettings | None = None) -> str:
    cfg = settings or get_settings()
    return "v2" if cfg.obligation_compare_prompt_v2_enabled else "v1"
