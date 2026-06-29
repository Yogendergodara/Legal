"""Coerce LLM quote fields to strings (Mistral structured-output drift)."""

from __future__ import annotations


def coerce_quote_field(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [str(x).strip() for x in value if str(x).strip()]
        return " ".join(parts)
    if isinstance(value, dict):
        for key in ("text", "quote", "content"):
            if key in value and value[key]:
                return str(value[key]).strip()
    return str(value).strip()


def coerce_optional_str(value: object) -> str:
    """Coerce nullable LLM ID fields to strings (Mistral null drift)."""
    if value is None:
        return ""
    return str(value).strip()
