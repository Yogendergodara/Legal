"""Per-parent policy section category tagging at ingest."""

from __future__ import annotations

import logging
from pathlib import Path

from document_core.config import DocumentCoreSettings, get_settings
from document_core.llm.ingest_llm import invoke_structured_json, llm_api_key_available
from document_core.schemas.category_tag import BatchSectionCategoryTagResult
from document_core.schemas.chunk import DocumentTree, SectionNode
from document_core.schemas.taxonomy import cap_section_categories, normalize_categories, taxonomy_prompt_labels
from document_core.services.document_tag_priors import apply_document_priors, document_prior_hint
from document_core.services.metadata_at_ingest import infer_section_categories_keyword

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "policy_section_categories.md"


def _iter_sections(nodes: list[SectionNode]):
    for node in nodes:
        yield node
        yield from _iter_sections(node.children)


def _finalize_categories(
    categories: list[str],
    *,
    document_title: str,
    settings: DocumentCoreSettings,
) -> list[str]:
    capped = cap_section_categories(
        apply_document_priors(categories, document_title=document_title),
        max_tags=settings.category_tagger_max_tags_per_section,
    )
    return capped or ["general"]


def apply_keyword_tags(
    tree: DocumentTree,
    *,
    document_title: str = "",
    settings: DocumentCoreSettings | None = None,
) -> None:
    cfg = settings or get_settings()
    title = document_title or getattr(tree, "title", "") or ""
    for node in _iter_sections(tree.sections):
        raw = infer_section_categories_keyword(title=node.title, text=node.text)
        node.categories = _finalize_categories(raw, document_title=title, settings=cfg)


def _sections_block(nodes: list[SectionNode], max_chars: int) -> str:
    lines: list[str] = []
    for node in nodes:
        body = node.text[:max_chars]
        lines.append(f"- section_id: {node.section_id} | title: {node.title}\n  text: {body}")
    return "\n".join(lines)


def _split_prompt(formatted: str) -> tuple[str, str]:
    parts = formatted.split("## USER", 1)
    system = parts[0].replace("## SYSTEM", "").strip()
    user = parts[1].strip() if len(parts) > 1 else ""
    return system, user


async def _tag_llm_batches(
    nodes: list[SectionNode],
    *,
    document_title: str,
    settings: DocumentCoreSettings,
) -> None:
    template = _PROMPT_PATH.read_text(encoding="utf-8")
    labels = taxonomy_prompt_labels()
    prior_hint = document_prior_hint(document_title) or "None."
    by_id = {node.section_id: node for node in nodes}

    for start in range(0, len(nodes), settings.category_tagger_batch_size):
        batch = nodes[start : start + settings.category_tagger_batch_size]
        prompt = template.format(
            taxonomy_labels=labels,
            document_title=document_title,
            prior_hint=prior_hint,
            sections_block=_sections_block(batch, settings.category_tagger_max_section_chars),
        )
        system, user = _split_prompt(prompt)
        result = await invoke_structured_json(
            model=settings.category_tagger_model,
            system=system,
            user=user,
            schema=BatchSectionCategoryTagResult,
            temperature=settings.category_tagger_temperature,
        )
        for item in result.items:
            node = by_id.get(item.section_id)
            if node is None:
                continue
            cats = normalize_categories(item.categories)
            if not cats or cats == ["general"]:
                cats = infer_section_categories_keyword(title=node.title, text=node.text)
            node.categories = _finalize_categories(
                cats,
                document_title=document_title,
                settings=settings,
            )

    for node in nodes:
        if not node.categories:
            raw = infer_section_categories_keyword(title=node.title, text=node.text)
            node.categories = _finalize_categories(
                raw,
                document_title=document_title,
                settings=settings,
            )


async def tag_policy_sections(
    tree: DocumentTree,
    *,
    document_title: str,
    settings: DocumentCoreSettings | None = None,
) -> tuple[DocumentTree, dict[str, object]]:
    """Tag each section in tree; return tree and ingest metadata extras."""
    cfg = settings or get_settings()
    nodes = list(_iter_sections(tree.sections))
    if not nodes:
        return tree, {"auto_tagged": True, "tagger": "keyword"}

    mode = cfg.category_tagger_mode
    use_llm = mode == "llm" or (mode == "auto" and llm_api_key_available())

    if use_llm and mode != "keyword":
        try:
            await _tag_llm_batches(nodes, document_title=document_title, settings=cfg)
            return tree, {"auto_tagged": True, "tagger": "llm"}
        except Exception as exc:
            logger.warning("category tagger LLM failed, using keyword fallback: %s", exc)

    apply_keyword_tags(tree, document_title=document_title, settings=cfg)
    return tree, {"auto_tagged": True, "tagger": "keyword"}
