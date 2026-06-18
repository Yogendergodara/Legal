"""Policy category taxonomy for metadata-filtered retrieval."""

from __future__ import annotations

# Standard policy families (extend via ingest metadata.categories).
STANDARD_POLICY_CATEGORIES: frozenset[str] = frozenset(
    {
        "security",
        "vendor_security",
        "privacy",
        "data_retention",
        "confidentiality",
        "indemnity",
        "liability",
        "termination",
        "ip",
        "employment",
        "hr",
        "procurement",
        "ai_usage",
        "governing_law",
        "payment",
        "sla",
        "insurance",
        "general",
    }
)


def normalize_categories(raw: list[str] | None) -> list[str]:
    """Lowercase, dedupe, drop empty category tags."""
    if not raw:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        key = (item or "").strip().lower().replace(" ", "_")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out
