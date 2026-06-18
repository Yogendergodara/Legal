"""Classify contract sections into policy category families."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from document_core.schemas.chunk import DocumentKind, IndexedChunk
from document_core.schemas.taxonomy import normalize_categories
from review_agent.config import ReviewSettings, get_settings
from review_agent.models.llm_gateway import get_review_model, invoke_structured
from review_agent.schemas.section_classify import SectionCategoryLLMResult, SectionCategoryResult

logger = logging.getLogger(__name__)

_HINTS_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "policy_category_hints.yaml"
)
_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "section_policy_classify.md"
)


def _load_hints() -> dict[str, list[str]]:
    if not _HINTS_PATH.is_file():
        return {}
    data = yaml.safe_load(_HINTS_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    return {str(k): list(v) for k, v in data.items() if isinstance(v, list)}


def _section_query(section: IndexedChunk) -> str:
    title = (section.title or section.section_id or "").strip()
    body = (section.text or "").strip()
    snippet = " ".join(body.split()[:24])
    if title and snippet:
        return f"{title} {snippet}"
    return title or snippet


def classify_section_lexical(section: IndexedChunk) -> SectionCategoryResult:
    """Keyword hints → policy categories (no LLM)."""
    hints = _load_hints()
    haystack = f"{section.title or ''} {section.text or ''}".lower()
    categories: list[str] = []
    for category, phrases in hints.items():
        if any(p.lower() in haystack for p in phrases):
            categories.append(category)
    query = _section_query(section)
    terms = [query]
    if section.title:
        terms.append(section.title.strip())
    return SectionCategoryResult(
        section_id=section.section_id,
        categories=normalize_categories(categories) or ["general"],
        query_terms=terms,
    )


async def classify_section_policies(
    section: IndexedChunk,
    *,
    contract_type: str | None = None,
    settings: ReviewSettings | None = None,
) -> SectionCategoryResult:
    cfg = settings or get_settings()
    if cfg.section_classify_mode == "lexical":
        return classify_section_lexical(section)

    system_tpl, user_tpl = _load_prompt_template()
    user = user_tpl.format(
        contract_type=contract_type or "unknown",
        section_id=section.section_id,
        section_title=section.title or section.section_id,
        section_text=(section.text or "")[: cfg.section_classify_max_chars],
    )
    try:
        model = get_review_model(
            temperature=cfg.compliance_llm_temperature,
            max_tokens=512,
        )
        result = await invoke_structured(
            model,
            SectionCategoryLLMResult,
            system=system_tpl,
            user=user,
        )
        categories = normalize_categories(result.categories) or ["general"]
        terms = result.query_terms or [_section_query(section)]
        return SectionCategoryResult(
            section_id=section.section_id,
            categories=categories,
            query_terms=terms,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("section classify LLM failed for %s: %s", section.section_id, exc)
        return classify_section_lexical(section)


def _load_prompt_template() -> tuple[str, str]:
    raw = _PROMPT_PATH.read_text(encoding="utf-8")
    if "## SYSTEM" not in raw or "## USER" not in raw:
        raise ValueError("section_policy_classify.md must contain ## SYSTEM and ## USER")
    _, system_block = raw.split("## SYSTEM", 1)
    system_text, user_block = system_block.split("## USER", 1)
    return system_text.strip(), user_block.strip()
