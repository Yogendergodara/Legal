"""Classify contract sections: lexical-first with LLM fallback."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from document_core.schemas.chunk import IndexedChunk
from document_core.schemas.taxonomy import normalize_categories, taxonomy_prompt_labels
from review_agent.config import ReviewSettings, get_settings
from review_agent.errors import FatalPipelineError, LLMUnavailableError
from review_agent.models.llm_gateway import get_review_model, invoke_structured, _is_rate_limit_error
from review_agent.schemas.section_classify import (
    BatchSectionCategoryLLMResult,
    SectionCategoryResult,
)
from review_agent.services.async_limits import gather_limited
from review_agent.services.section_category_lexical import (
    infer_lexical_classify,
    infer_query_terms_from_lexical,
)
from review_agent.services.section_cross_reference import (
    RelatedSectionBundle,
    build_classification_context,
    resolve_all_related_sections,
)
from review_agent.services.section_gap_status import is_non_substantive_section
from review_agent.resilience.failure_policy import note_batch_llm_failure, should_batch_single_retry

logger = logging.getLogger(__name__)

_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "section_policy_classify.md"
)

_BATCH_APPENDIX = """

### Batch output (when multiple sections appear in the user message)

Return JSON only — one entry per section_id:
```json
{
  "items": [
    {
      "section_id": "5",
      "categories": ["termination", "confidentiality"],
      "query_terms": ["term and survival", "confidentiality period"]
    }
  ]
}
```
"""

_SUBSTANTIVE_TITLE = re.compile(
    r"liabilit|indemn|confidential|terminat|privacy|data|security|"
    r"intellectual property|\bip\b|governing law|payment|insurance|sla",
    re.IGNORECASE,
)

def _section_query(section: IndexedChunk) -> str:
    title = (section.title or section.section_id or "").strip()
    body = (section.text or "").strip()
    snippet = " ".join(body.split()[:24])
    if title and snippet:
        return f"{title} {snippet}"
    return title or snippet


def _load_prompt_template() -> tuple[str, str]:
    raw = _PROMPT_PATH.read_text(encoding="utf-8")
    raw = raw.replace("{taxonomy_labels}", taxonomy_prompt_labels())
    if "## SYSTEM" not in raw or "## USER" not in raw:
        raise ValueError("section_policy_classify.md must contain ## SYSTEM and ## USER")
    _, system_block = raw.split("## SYSTEM", 1)
    system_text, user_block = system_block.split("## USER", 1)
    return system_text.strip(), user_block.strip()


def _lexical_with_context(
    section: IndexedChunk,
    context_text: str,
    *,
    settings: ReviewSettings,
):
    return infer_lexical_classify(
        section,
        context_text=context_text,
        body_scan_chars=settings.section_lexical_body_scan_chars,
        full_body_max_chars=settings.section_lexical_full_body_max_chars,
    )


def _enrich_categories_from_lexical(
    categories: list[str],
    section: IndexedChunk,
    *,
    context_text: str = "",
    settings: ReviewSettings | None = None,
) -> tuple[list[str], str | None]:
    cfg = settings or get_settings()
    if categories != ["general"]:
        return categories, None
    lex = _lexical_with_context(section, context_text, settings=cfg)
    enriched = normalize_categories(lex.categories) or categories
    if enriched == categories:
        return categories, None
    return enriched, f"lexical_enriched={enriched}"


def _resolve_categories_and_terms(
    section: IndexedChunk,
    *,
    raw_categories: list[str] | None,
    llm_query_terms: list[str] | None = None,
    context_text: str = "",
    settings: ReviewSettings | None = None,
) -> tuple[list[str], list[str], str | None]:
    """Normalize LLM categories; enrich or override general via extended lexical."""
    cfg = settings or get_settings()
    categories = normalize_categories(raw_categories or []) or ["general"]
    note: str | None = None

    categories, enrich_note = _enrich_categories_from_lexical(
        categories,
        section,
        context_text=context_text,
        settings=cfg,
    )
    if enrich_note:
        note = enrich_note

    if categories == ["general"]:
        lex = _lexical_with_context(section, context_text, settings=cfg)
        if lex.categories:
            categories = normalize_categories(lex.categories)
            note = f"lexical_override_general={categories}"

    title = (section.title or "").strip()
    if (
        cfg.section_classify_block_general_substantive
        and categories == ["general"]
        and title
        and _SUBSTANTIVE_TITLE.search(title)
    ):
        lex = _lexical_with_context(section, context_text, settings=cfg)
        if lex.categories:
            categories = normalize_categories(lex.categories)
            note = f"substantive_title_lexical={categories}"

    if categories != ["general"]:
        terms = infer_query_terms_from_lexical(categories, section)
    elif llm_query_terms:
        terms = list(llm_query_terms)
    else:
        terms = [_section_query(section)]

    return categories, terms, note


def _boilerplate_classify_result(
    section: IndexedChunk,
    bundle: RelatedSectionBundle | None,
) -> SectionCategoryResult:
    related_ids = [sid for sid, _, _ in (bundle.related if bundle else [])]
    return SectionCategoryResult(
        section_id=section.section_id,
        categories=["general"],
        query_terms=[],
        substantive=False,
        related_section_ids=related_ids,
        classify_warning="boilerplate_skip",
    )


def _lexical_classify_result(
    section: IndexedChunk,
    *,
    settings: ReviewSettings,
    context_text: str = "",
    bundle: RelatedSectionBundle | None = None,
) -> SectionCategoryResult | None:
    """Return full result if LLM can be skipped; None if LLM required."""
    if settings.section_classify_mode != "lexical_first":
        return None
    if is_non_substantive_section(section):
        return None
    lex = _lexical_with_context(section, context_text, settings=settings)
    if lex.confidence not in ("title", "body") or not lex.categories:
        return None
    if lex.categories == ["general"]:
        return None
    related_ids = [sid for sid, _, _ in (bundle.related if bundle else [])]
    return SectionCategoryResult(
        section_id=section.section_id,
        categories=lex.categories,
        query_terms=infer_query_terms_from_lexical(lex.categories, section),
        substantive=True,
        related_section_ids=related_ids,
        classify_warning=f"lexical_first={lex.confidence}:{lex.categories}",
    )


def _fallback_result(
    section: IndexedChunk,
    *,
    reason: str,
    settings: ReviewSettings | None = None,
    context_text: str = "",
    bundle: RelatedSectionBundle | None = None,
) -> SectionCategoryResult:
    cfg = settings or get_settings()
    categories, terms, note = _resolve_categories_and_terms(
        section,
        raw_categories=["general"],
        context_text=context_text,
        settings=cfg,
    )
    if categories != ["general"]:
        warning = f"{reason}; lexical_fallback={categories}"
    else:
        warning = reason
    if note:
        warning = f"{warning}; {note}" if warning else note

    logger.warning(
        "section classifier fallback for %s: %s (categories=%s)",
        section.section_id,
        reason,
        categories,
    )
    related_ids = [sid for sid, _, _ in (bundle.related if bundle else [])]
    return SectionCategoryResult(
        section_id=section.section_id,
        categories=categories,
        query_terms=terms,
        substantive=True,
        related_section_ids=related_ids,
        classify_warning=warning,
    )


def _normalize_classify_payload(
    raw: Any,
    sections: list[IndexedChunk],
) -> BatchSectionCategoryLLMResult:
    items_raw: list[Any]
    if isinstance(raw, list):
        items_raw = raw
    elif isinstance(raw, dict):
        if "items" in raw:
            items_raw = raw["items"]
        elif "categories" in raw or "section_id" in raw:
            items_raw = [raw]
        else:
            items_raw = []
    else:
        items_raw = []

    out_items: list[SectionCategoryResult] = []
    for idx, entry in enumerate(items_raw):
        if not isinstance(entry, dict):
            continue
        section_id = str(entry.get("section_id") or "").strip()
        if not section_id and idx < len(sections):
            section_id = sections[idx].section_id
        if not section_id:
            continue
        out_items.append(
            SectionCategoryResult(
                section_id=section_id,
                categories=list(entry.get("categories") or []),
                query_terms=list(entry.get("query_terms") or []),
            )
        )
    return BatchSectionCategoryLLMResult(items=out_items)


async def _salvage_classify_batch(
    sections: list[IndexedChunk],
    *,
    contract_type: str | None,
    settings: ReviewSettings,
    system_tpl: str,
    batch_user: str,
) -> BatchSectionCategoryLLMResult:
    _ = contract_type
    model = get_review_model(
        temperature=settings.compliance_llm_temperature,
        max_tokens=1024 if len(sections) > 1 else 512,
    )
    result = await invoke_structured(
        model,
        BatchSectionCategoryLLMResult,
        system=system_tpl,
        user=batch_user,
    )
    if result.items:
        return result
    return _normalize_classify_payload({"items": []}, sections)


def _rate_limited_classify_results(
    sections: list[IndexedChunk],
    *,
    settings: ReviewSettings,
    context_by_id: dict[str, str],
    bundles_by_id: dict[str, RelatedSectionBundle],
) -> dict[str, SectionCategoryResult]:
    return {
        section.section_id: (
            _lexical_classify_result(
                section,
                settings=settings,
                context_text=context_by_id.get(section.section_id, ""),
                bundle=bundles_by_id.get(section.section_id),
            )
            or _fallback_result(
                section,
                reason="rate_limited",
                settings=settings,
                context_text=context_by_id.get(section.section_id, ""),
                bundle=bundles_by_id.get(section.section_id),
            )
        )
        for section in sections
    }


async def _classify_batch_llm(
    sections: list[IndexedChunk],
    *,
    contract_type: str | None,
    settings: ReviewSettings,
    context_by_id: dict[str, str],
    bundles_by_id: dict[str, RelatedSectionBundle],
) -> dict[str, SectionCategoryResult]:
    if not sections:
        return {}

    system_tpl, _user_tpl = _load_prompt_template()
    if len(sections) > 1:
        system_tpl = system_tpl + _BATCH_APPENDIX

    blocks: list[str] = []
    for section in sections:
        text = (section.text or "")[: settings.section_classify_max_chars]
        blocks.append(
            f"### Section {section.section_id} — {section.title}\n```\n{text}\n```"
        )
    batch_user = (
        f"Contract type: {contract_type or 'unknown'}\n\n"
        f"Classify each section below. Return one item per section_id.\n\n"
        + "\n\n".join(blocks)
    )
    max_tokens = 512 if len(sections) == 1 else 1024
    model = get_review_model(
        temperature=settings.compliance_llm_temperature,
        max_tokens=max_tokens,
    )
    try:
        result = await invoke_structured(
            model,
            BatchSectionCategoryLLMResult,
            system=system_tpl,
            user=batch_user,
        )
    except FatalPipelineError:
        raise
    except LLMUnavailableError as exc:
        logger.warning("batch section classify LLM unavailable: %s", exc)
        return {
            section.section_id: (
                _lexical_classify_result(
                    section,
                    settings=settings,
                    context_text=context_by_id.get(section.section_id, ""),
                    bundle=bundles_by_id.get(section.section_id),
                )
                or _fallback_result(
                    section,
                    reason="llm_unavailable",
                    settings=settings,
                    context_text=context_by_id.get(section.section_id, ""),
                    bundle=bundles_by_id.get(section.section_id),
                )
            )
            for section in sections
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("batch section classify structured failed: %s", exc)
        if _is_rate_limit_error(exc):
            return _rate_limited_classify_results(
                sections,
                settings=settings,
                context_by_id=context_by_id,
                bundles_by_id=bundles_by_id,
            )
        try:
            result = await _salvage_classify_batch(
                sections,
                contract_type=contract_type,
                settings=settings,
                system_tpl=system_tpl,
                batch_user=batch_user,
            )
        except FatalPipelineError:
            raise
        except Exception as salvage_exc:  # noqa: BLE001
            logger.warning("batch section classify salvage failed: %s", salvage_exc)
            if _is_rate_limit_error(salvage_exc):
                return _rate_limited_classify_results(
                    sections,
                    settings=settings,
                    context_by_id=context_by_id,
                    bundles_by_id=bundles_by_id,
                )
            note_batch_llm_failure()
            if not settings.section_classify_batch_retry_single or len(sections) == 1:
                return {
                    section.section_id: _fallback_result(
                        section,
                        reason=str(salvage_exc) or str(exc) or "batch classify failed",
                        settings=settings,
                        context_text=context_by_id.get(section.section_id, ""),
                        bundle=bundles_by_id.get(section.section_id),
                    )
                    for section in sections
                }
            if not should_batch_single_retry(
                salvage_exc,
                batch_len=len(sections),
                batch_retry_enabled=True,
                posture_enabled=settings.llm_review_posture_enabled,
            ):
                return {
                    section.section_id: _fallback_result(
                        section,
                        reason=str(salvage_exc) or str(exc) or "batch classify failed",
                        settings=settings,
                        context_text=context_by_id.get(section.section_id, ""),
                        bundle=bundles_by_id.get(section.section_id),
                    )
                    for section in sections
                }

            out: dict[str, SectionCategoryResult] = {}
            for section in sections:
                try:
                    single = await _classify_batch_llm(
                        [section],
                        contract_type=contract_type,
                        settings=settings,
                        context_by_id=context_by_id,
                        bundles_by_id=bundles_by_id,
                    )
                    out[section.section_id] = single[section.section_id]
                except FatalPipelineError:
                    raise
                except Exception as single_exc:  # noqa: BLE001
                    out[section.section_id] = _fallback_result(
                        section,
                        reason=f"batch_and_single_failed:{single_exc}",
                        settings=settings,
                        context_text=context_by_id.get(section.section_id, ""),
                        bundle=bundles_by_id.get(section.section_id),
                    )
            return out

    out: dict[str, SectionCategoryResult] = {}
    for item in result.items:
        section = next((s for s in sections if s.section_id == item.section_id), None)
        if section is None:
            continue
        ctx = context_by_id.get(section.section_id, "")
        bundle = bundles_by_id.get(section.section_id)
        categories, terms, note = _resolve_categories_and_terms(
            section,
            raw_categories=item.categories,
            llm_query_terms=item.query_terms,
            context_text=ctx,
            settings=settings,
        )
        related_ids = [sid for sid, _, _ in (bundle.related if bundle else [])]
        out[item.section_id] = SectionCategoryResult(
            section_id=item.section_id,
            categories=categories,
            query_terms=terms,
            substantive=True,
            related_section_ids=related_ids,
            classify_warning=note,
        )
    for section in sections:
        if section.section_id not in out:
            out[section.section_id] = _fallback_result(
                section,
                reason="classifier omitted section in batch response",
                settings=settings,
                context_text=context_by_id.get(section.section_id, ""),
                bundle=bundles_by_id.get(section.section_id),
            )
    return out


async def classify_sections_batch(
    sections: list[IndexedChunk],
    *,
    contract_type: str | None = None,
    settings: ReviewSettings | None = None,
    all_sections: list[IndexedChunk] | None = None,
    related_bundles: dict[str, RelatedSectionBundle] | None = None,
) -> dict[str, SectionCategoryResult]:
    cfg = settings or get_settings()
    if not sections:
        return {}

    corpus = all_sections if all_sections is not None else sections
    bundles_by_id = related_bundles
    if bundles_by_id is None and cfg.section_cross_ref_enabled:
        bundles_by_id = resolve_all_related_sections(corpus, settings=cfg)
    bundles_by_id = bundles_by_id or {}

    context_by_id = {
        sid: build_classification_context(bundle)
        for sid, bundle in bundles_by_id.items()
    }

    out: dict[str, SectionCategoryResult] = {}
    needs_llm: list[IndexedChunk] = []

    for section in sections:
        if cfg.gap_boilerplate_skip_compare and is_non_substantive_section(section):
            out[section.section_id] = _boilerplate_classify_result(
                section,
                bundles_by_id.get(section.section_id),
            )
            continue

        lexical = _lexical_classify_result(
            section,
            settings=cfg,
            context_text=context_by_id.get(section.section_id, ""),
            bundle=bundles_by_id.get(section.section_id),
        )
        if lexical is not None:
            out[section.section_id] = lexical
        else:
            needs_llm.append(section)

    if needs_llm:
        out.update(
            await _classify_batch_llm(
                needs_llm,
                contract_type=contract_type,
                settings=cfg,
                context_by_id=context_by_id,
                bundles_by_id=bundles_by_id,
            )
        )
    return out


async def classify_section_policies(
    section: IndexedChunk,
    *,
    contract_type: str | None = None,
    settings: ReviewSettings | None = None,
    all_sections: list[IndexedChunk] | None = None,
) -> SectionCategoryResult:
    cfg = settings or get_settings()
    results = await classify_sections_batch(
        [section],
        contract_type=contract_type,
        settings=cfg,
        all_sections=all_sections,
    )
    return results.get(section.section_id) or _fallback_result(
        section,
        reason="missing classify result",
        settings=cfg,
    )


async def classify_all_sections(
    sections: list[IndexedChunk],
    *,
    contract_type: str | None = None,
    settings: ReviewSettings | None = None,
) -> tuple[dict[str, SectionCategoryResult], dict[str, int]]:
    cfg = settings or get_settings()
    related_bundles = (
        resolve_all_related_sections(sections, settings=cfg)
        if cfg.section_cross_ref_enabled
        else {}
    )

    batch_size = max(1, cfg.section_classify_batch_size)
    batches = [sections[i : i + batch_size] for i in range(0, len(sections), batch_size)]

    async def run_batch(batch: list[IndexedChunk]):
        return await classify_sections_batch(
            batch,
            contract_type=contract_type,
            settings=cfg,
            all_sections=sections,
            related_bundles=related_bundles,
        )

    results = await gather_limited(
        [run_batch(batch) for batch in batches],
        limit=cfg.section_compare_concurrency,
    )

    merged: dict[str, SectionCategoryResult] = {}

    for batch, result in zip(batches, results, strict=True):
        if isinstance(result, BaseException):
            logger.warning("classify batch failed: %s", result)
            for section in batch:
                merged[section.section_id] = _fallback_result(
                    section,
                    reason=str(result),
                    settings=cfg,
                    context_text=build_classification_context(
                        related_bundles.get(section.section_id)
                    ),
                    bundle=related_bundles.get(section.section_id),
                )
            continue
        merged.update(result)

    classify_stats = {
        "classify_lexical_skipped": 0,
        "classify_llm_sections": 0,
        "classify_boilerplate_skipped": 0,
    }
    for result in merged.values():
        warning = result.classify_warning or ""
        if warning == "boilerplate_skip" or not result.substantive:
            classify_stats["classify_boilerplate_skipped"] += 1
        elif warning.startswith("lexical_first="):
            classify_stats["classify_lexical_skipped"] += 1
        else:
            classify_stats["classify_llm_sections"] += 1

    return merged, classify_stats
