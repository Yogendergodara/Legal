"""Per-parent policy section category tagging at ingest."""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from document_core.config import DocumentCoreSettings, get_settings
from document_core.llm.ingest_llm import invoke_structured_json, llm_api_key_available
from document_core.schemas.category_tag import BatchSectionCategoryTagResult
from document_core.schemas.chunk import DocumentTree, SectionNode
from document_core.schemas.taxonomy import (
    STANDARD_POLICY_CATEGORIES,
    BROAD_POLICY_CATEGORIES,
    cap_section_categories,
    normalize_categories,
    taxonomy_prompt_grouped,
)
from document_core.services.document_tag_priors import apply_document_priors, document_prior_hint
from document_core.services.metadata_at_ingest import infer_section_categories_keyword

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "policy_section_categories.md"
_SECTION_LINE_OVERHEAD = 96
_ALLOWED_TAGS = STANDARD_POLICY_CATEGORIES - {"general"}


@lru_cache(maxsize=1)
def _prompt_template() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _sanitize_llm_categories(
    categories: list[str],
    *,
    node: SectionNode,
) -> list[str]:
    """Drop hallucinated labels; keyword-fill when LLM returns only broad/empty tags."""
    norm = normalize_categories(categories)
    valid = [cat for cat in norm if cat in _ALLOWED_TAGS]
    specific = [cat for cat in valid if cat not in BROAD_POLICY_CATEGORIES]
    if specific:
        broad = [cat for cat in valid if cat in BROAD_POLICY_CATEGORIES]
        return specific + broad
    if valid:
        return valid
    return infer_section_categories_keyword(title=node.title, text=node.text)


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


def _per_section_char_budget(
    batch: list[SectionNode],
    *,
    max_chars_per_section: int,
    max_total_chars: int | None = None,
) -> int:
    if not batch:
        return max_chars_per_section
    if max_total_chars is None:
        return max_chars_per_section
    even = max_total_chars // len(batch)
    return max(400, min(max_chars_per_section, even))


def _estimate_batch_chars(
    batch: list[SectionNode],
    *,
    max_chars_per_section: int,
) -> int:
    if not batch:
        return 0
    budget = _per_section_char_budget(
        batch,
        max_chars_per_section=max_chars_per_section,
        max_total_chars=None,
    )
    body = sum(min(len(node.text or ""), budget) + _SECTION_LINE_OVERHEAD for node in batch)
    return body + 256


def plan_llm_batches(
    nodes: list[SectionNode],
    *,
    settings: DocumentCoreSettings,
) -> list[list[SectionNode]]:
    """Prefer one whole-policy LLM call; else split into batches of at least batch_size."""
    if not nodes:
        return []

    batch_size = max(1, settings.category_tagger_batch_size)
    max_policy_chars = settings.category_tagger_whole_policy_max_chars
    max_section_chars = settings.category_tagger_max_section_chars

    if settings.category_tagger_whole_policy_enabled:
        if _estimate_batch_chars(nodes, max_chars_per_section=max_section_chars) <= max_policy_chars:
            return [nodes]

    if len(nodes) <= batch_size:
        return [nodes]

    batches: list[list[SectionNode]] = []
    start = 0
    while start < len(nodes):
        remaining = len(nodes) - start
        if remaining <= batch_size:
            batches.append(nodes[start:])
            break
        chunk = nodes[start : start + batch_size]
        if _estimate_batch_chars(chunk, max_chars_per_section=max_section_chars) > max_policy_chars:
            half = max(1, len(chunk) // 2)
            batches.append(nodes[start : start + half])
            start += half
            continue
        batches.append(chunk)
        start += batch_size
    return batches


def _sections_block(
    nodes: list[SectionNode],
    *,
    max_chars_per_section: int,
    max_total_chars: int | None = None,
) -> str:
    budget = _per_section_char_budget(
        nodes,
        max_chars_per_section=max_chars_per_section,
        max_total_chars=max_total_chars,
    )
    lines: list[str] = []
    for node in nodes:
        raw = node.text or ""
        body = raw[:budget]
        truncated = len(raw.strip()) > len(body.strip())
        title = (node.title or node.section_id or "").strip()
        lines.append(
            f"section_id: {node.section_id}\n"
            f"title: {title}\n"
            f"text: {body}{' …[truncated]' if truncated else ''}"
        )
    return "\n---\n".join(lines)


def _split_prompt(formatted: str) -> tuple[str, str]:
    parts = formatted.split("## USER", 1)
    system = parts[0].replace("## SYSTEM", "").strip()
    user = parts[1].strip() if len(parts) > 1 else ""
    return system, user


async def _tag_llm_batch(
    batch: list[SectionNode],
    *,
    document_title: str,
    settings: DocumentCoreSettings,
    by_id: dict[str, SectionNode],
) -> None:
    template = _prompt_template()
    prior_hint = document_prior_hint(document_title) or "Family hint: none."
    max_policy_chars = settings.category_tagger_whole_policy_max_chars
    sections_block = _sections_block(
        batch,
        max_chars_per_section=settings.category_tagger_max_section_chars,
        max_total_chars=max_policy_chars,
    )
    prompt = template.format(
        taxonomy_groups=taxonomy_prompt_grouped(),
        document_title=document_title,
        prior_hint=prior_hint,
        section_count=len(batch),
        sections_block=sections_block,
    )
    system, user = _split_prompt(prompt)
    max_tokens = min(8192, 96 * len(batch) + 384)
    result = await invoke_structured_json(
        model=settings.category_tagger_model,
        system=system,
        user=user,
        schema=BatchSectionCategoryTagResult,
        temperature=settings.category_tagger_temperature,
        timeout_seconds=settings.category_tagger_llm_timeout_seconds,
        max_tokens=max_tokens,
    )
    seen_ids: set[str] = set()
    for item in result.items:
        node = by_id.get(item.section_id)
        if node is None:
            continue
        seen_ids.add(item.section_id)
        cats = _sanitize_llm_categories(item.categories, node=node)
        node.categories = _finalize_categories(
            cats,
            document_title=document_title,
            settings=settings,
        )

    for node in batch:
        if node.section_id in seen_ids and node.categories:
            continue
        if node.section_id not in seen_ids:
            logger.warning(
                "category tagger: LLM omitted section %s in batch for %r",
                node.section_id,
                document_title,
            )
        raw = infer_section_categories_keyword(title=node.title, text=node.text)
        node.categories = _finalize_categories(
            raw,
            document_title=document_title,
            settings=settings,
        )


async def _tag_llm_batches(
    nodes: list[SectionNode],
    *,
    document_title: str,
    settings: DocumentCoreSettings,
) -> None:
    by_id = {node.section_id: node for node in nodes}
    batches = plan_llm_batches(nodes, settings=settings)
    logger.info(
        "category tagger: %d section(s) in %d LLM call(s) for %r",
        len(nodes),
        len(batches),
        document_title,
    )
    for batch_index, batch in enumerate(batches):
        try:
            await _tag_llm_batch(
                batch,
                document_title=document_title,
                settings=settings,
                by_id=by_id,
            )
        except Exception as exc:
            if len(batch) == 1:
                raise
            logger.warning(
                "category tagger LLM failed for batch %s/%s (%d sections), splitting: %s",
                batch_index + 1,
                len(batches),
                len(batch),
                exc,
            )
            mid = len(batch) // 2
            await _tag_llm_batches(batch[:mid], document_title=document_title, settings=settings)
            await _tag_llm_batches(batch[mid:], document_title=document_title, settings=settings)

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
