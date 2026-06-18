"""Optional LLM filter for dynamic review categories (Phase 3)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from document_core.schemas.chunk import IndexedChunk
from pydantic import ValidationError

from review_agent.config import ReviewSettings
from review_agent.models.llm_gateway import get_review_model, invoke_structured
from review_agent.schemas.policy_plan_llm import PolicyPlanFilterResult
from review_agent.schemas.review_category import ReviewCategory

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "policy_plan.md"


def _load_prompt_template() -> tuple[str, str]:
    raw = _PROMPT_PATH.read_text(encoding="utf-8")
    if "## SYSTEM" not in raw or "## USER" not in raw:
        raise ValueError("policy_plan.md must contain ## SYSTEM and ## USER sections")
    _, system_block = raw.split("## SYSTEM", 1)
    system_text, user_block = system_block.split("## USER", 1)
    return system_text.strip(), user_block.strip()


def _categories_json(
    categories: list[ReviewCategory],
    policy_titles_by_doc: dict[str, str],
) -> str:
    items = [
        {
            "id": category.category_id,
            "label": category.label,
            "policy_title": policy_titles_by_doc.get(str(category.policy_document_id), ""),
        }
        for category in categories
    ]
    return json.dumps(items, indent=2)


def _contract_section_titles(contract_sections: list[IndexedChunk]) -> str:
    if not contract_sections:
        return "_No structured contract sections detected._"
    lines = [f"- {section.title or section.section_id}" for section in contract_sections]
    return "\n".join(lines)


def _apply_filter_result(
    categories: list[ReviewCategory],
    result: PolicyPlanFilterResult,
) -> list[ReviewCategory]:
    """Keep valid IDs in original order; fail-open if filter empty."""
    valid_ids = {category.category_id for category in categories}
    selected = [cid for cid in result.relevant_category_ids if cid in valid_ids]

    if not selected:
        return list(categories)

    selected_set = set(selected)
    filtered: list[ReviewCategory] = []
    for category in categories:
        if category.category_id not in selected_set:
            continue
        overrides = result.search_query_overrides.get(category.category_id)
        if overrides:
            category = category.model_copy(update={"search_queries": list(overrides)})
        filtered.append(category)
    return filtered


async def filter_categories_llm(
    *,
    categories: list[ReviewCategory],
    contract_sections: list[IndexedChunk],
    contract_type: str | None,
    policy_titles_by_doc: dict[str, str],
    settings: ReviewSettings,
) -> list[ReviewCategory]:
    """Filter pre-built categories with LLM; fail-open on error or empty selection."""
    if not settings.review_plan_llm_filter or not categories:
        return categories

    if len(categories) <= settings.review_plan_llm_filter_min_categories:
        return categories

    system_template, user_template = _load_prompt_template()
    user_message = user_template.format(
        contract_type=contract_type or "unspecified",
        contract_section_titles=_contract_section_titles(contract_sections),
        categories_json=_categories_json(categories, policy_titles_by_doc),
    )

    model = get_review_model(
        temperature=settings.review_plan_llm_temperature,
        max_tokens=settings.review_plan_llm_max_tokens,
    )

    last_error: str | None = None
    for attempt in range(settings.review_plan_llm_max_retries + 1):
        try:
            result = await invoke_structured(
                model,
                PolicyPlanFilterResult,
                system=system_template,
                user=user_message,
            )
            return _apply_filter_result(categories, result)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            logger.warning(
                "policy plan LLM filter parse failed attempt=%s: %s",
                attempt + 1,
                exc,
            )
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            logger.warning(
                "policy plan LLM filter invoke failed attempt=%s: %s",
                attempt + 1,
                exc,
            )

    logger.warning(
        "policy plan LLM filter failed; using all %s categories: %s",
        len(categories),
        last_error,
    )
    return categories
