"""Load generic review dimensions (search intents only)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from review_agent.schemas.review_category import ReviewCategory

_DIMENSIONS_PATH = Path(__file__).resolve().parent.parent / "dimensions" / "review_dimensions.yaml"


def load_dimensions(path: Path | None = None) -> dict[str, dict[str, Any]]:
    target = path or _DIMENSIONS_PATH
    with target.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError("review_dimensions.yaml must be a mapping")
    return data


def yaml_to_categories(
    dimensions: dict[str, dict[str, Any]],
) -> tuple[list["ReviewCategory"], list[str]]:
    """Legacy static checklist → ReviewCategory list (search-only, no fixed policy doc)."""
    from review_agent.schemas.review_category import ReviewCategory

    categories: list[ReviewCategory] = []
    for dimension_id, spec in dimensions.items():
        label = spec.get("label", dimension_id)
        queries = spec.get("search_queries") or [label]
        categories.append(
            ReviewCategory(
                category_id=dimension_id,
                label=label,
                policy_document_id=None,
                policy_section_id="",
                search_queries=list(queries),
                review_guidance=spec.get("review_guidance", "") or "",
                source="yaml_static",
            )
        )
    return categories, []
