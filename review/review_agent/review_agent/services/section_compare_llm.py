"""Section-first LLM compliance compare (production pipeline)."""

from __future__ import annotations

import logging
from pathlib import Path

from document_core.schemas.chunk import IndexedChunk, RetrievalHit
from document_core.schemas.compliance import ComplianceStatus, Severity
from review_agent.config import ReviewSettings, get_settings
from review_agent.errors import FatalPipelineError, LLMUnavailableError
from review_agent.models.llm_gateway import get_review_model, invoke_structured
from review_agent.schemas.compliance_llm import ComplianceLLMResult
from review_agent.schemas.section_compare import BatchSectionCompareLLMResult, SectionCompareItem
from review_agent.services.async_limits import gather_limited
from review_agent.services.compare_hit_selection import filter_hits_for_compare
from review_agent.services.playbook_context import PlaybookHints, format_playbook_hint_block, hints_from_chunk_metadata
from review_agent.services.quote_validate import truncate_section, validate_and_normalize_quotes
from review_agent.services.token_budget import split_batch_by_token_budget

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "section_compare.md"


def _load_prompt_template() -> tuple[str, str]:
    raw = _PROMPT_PATH.read_text(encoding="utf-8")
    if "## SYSTEM" not in raw or "## USER" not in raw:
        raise ValueError("section_compare.md must contain ## SYSTEM and ## USER")
    _, system_block = raw.split("## SYSTEM", 1)
    system_text, user_block = system_block.split("## USER", 1)
    return system_text.strip(), user_block.strip()


def _hit_lookup(hits_by_section: dict[str, list[RetrievalHit]]) -> dict[str, RetrievalHit]:
    lookup: dict[str, RetrievalHit] = {}
    for sid, hits in hits_by_section.items():
        for hit in hits:
            parent = hit.parent_chunk
            lookup[f"{sid}:{parent.document_id}:{parent.section_id}"] = hit
            lookup[f"{sid}::{parent.section_id}"] = hit
            lookup[f"{sid}:{parent.document_id}:"] = hit
    return lookup


def _resolve_policy_text(
    item: SectionCompareItem,
    *,
    hits_by_section: dict[str, list[RetrievalHit]],
    hit_lookup: dict[str, RetrievalHit],
) -> str:
    policy_key = f"{item.section_id}:{item.policy_document_id}:{item.policy_section_id}"
    hit = hit_lookup.get(policy_key)
    if hit is None and item.policy_section_id:
        hit = hit_lookup.get(f"{item.section_id}::{item.policy_section_id}")
    if hit is None and item.policy_document_id:
        hit = hit_lookup.get(f"{item.section_id}:{item.policy_document_id}:")
    if hit is None:
        for candidate in hits_by_section.get(item.section_id) or []:
            parent = candidate.parent_chunk
            if item.policy_document_id and str(parent.document_id) != item.policy_document_id:
                continue
            if item.policy_section_id and parent.section_id != item.policy_section_id:
                continue
            hit = candidate
            break
    if hit is None:
        return ""
    return hit.parent_chunk.text or ""


def _backfill_policy_ids(
    item: SectionCompareItem,
    *,
    hits_by_section: dict[str, list[RetrievalHit]],
) -> SectionCompareItem:
    if item.policy_document_id:
        return item
    hits = hits_by_section.get(item.section_id) or []
    if not hits:
        return item
    if item.policy_section_id:
        for hit in hits:
            if hit.parent_chunk.section_id == item.policy_section_id:
                return item.model_copy(
                    update={"policy_document_id": str(hit.parent_chunk.document_id)}
                )
    if len(hits) == 1:
        return item.model_copy(
            update={
                "policy_document_id": str(hits[0].parent_chunk.document_id),
                "policy_section_id": hits[0].parent_chunk.section_id,
            }
        )
    return item


def _format_sections_block(
    sections: list[IndexedChunk],
    hits_by_section: dict[str, list[RetrievalHit]],
    *,
    max_section_chars: int,
    playbook_hints_by_document: dict[str, PlaybookHints] | None = None,
    enrich_playbook: bool = True,
) -> tuple[str, list[str]]:
    blocks: list[str] = []
    truncated_ids: list[str] = []
    hints_map = playbook_hints_by_document or {}
    for section in sections:
        body = section.text or ""
        if len(body.strip()) > max_section_chars:
            truncated_ids.append(section.section_id)
        blocks.append(f"### Contract section: {section.section_id} — {section.title}")
        blocks.append(f"```\n{truncate_section(body, max_section_chars)}\n```")
        policy_hits = hits_by_section.get(section.section_id) or []
        if not policy_hits:
            blocks.append("- **Policies:** [none retrieved]")
            continue
        for idx, hit in enumerate(policy_hits, start=1):
            parent = hit.parent_chunk
            ptext = parent.text or ""
            if len(ptext.strip()) > max_section_chars:
                truncated_ids.append(section.section_id)
            header = (
                f"- **Policy {idx}** doc={parent.document_id} section={parent.section_id} "
                f"title={parent.title}"
            )
            hint_block = ""
            if enrich_playbook:
                hints = hints_map.get(str(parent.document_id))
                if hints is None:
                    hints = hints_from_chunk_metadata(parent.metadata)
                hint_block = format_playbook_hint_block(hints)
            blocks.append(header)
            if hint_block:
                blocks.append(hint_block)
            blocks.append(f"```\n{truncate_section(ptext, max_section_chars)}\n```")
    return "\n\n".join(blocks), truncated_ids


def _failure_items(sections: list[IndexedChunk], *, reason: str) -> list[SectionCompareItem]:
    return [
        SectionCompareItem(
            section_id=section.section_id,
            dimension_label=section.title or section.section_id,
            status=ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
            severity=Severity.INFO,
            rationale=f"Section compare failed: {reason}"[:2000],
        )
        for section in sections
    ]


def _normalize_item_quotes(
    item: SectionCompareItem,
    *,
    section_text: str,
    policy_text: str,
    quote_stats: dict[str, int] | None = None,
    anchor_enabled: bool = True,
) -> SectionCompareItem:
    adapted = ComplianceLLMResult(
        status=item.status,
        severity=item.severity,
        contract_quote=item.contract_quote,
        policy_quote=item.policy_quote,
        rationale=item.rationale,
        confidence=item.confidence,
    )
    normalized = validate_and_normalize_quotes(
        adapted,
        contract_text=section_text,
        policy_text=policy_text,
        quote_stats=quote_stats,
        anchor_enabled=anchor_enabled,
    )
    return item.model_copy(
        update={
            "status": normalized.status,
            "severity": normalized.severity,
            "contract_quote": normalized.contract_quote,
            "policy_quote": normalized.policy_quote,
            "rationale": normalized.rationale,
            "confidence": normalized.confidence,
        }
    )


def _postprocess_compare_items(
    items: list[SectionCompareItem],
    sections: list[IndexedChunk],
    hits_by_section: dict[str, list[RetrievalHit]],
    *,
    quote_stats: dict[str, int] | None = None,
    anchor_enabled: bool = True,
    warnings: list[str] | None = None,
) -> list[SectionCompareItem]:
    section_text_by_id = {s.section_id: s.text or "" for s in sections}
    batch_section_ids = set(section_text_by_id)
    hit_lookup = _hit_lookup(hits_by_section)
    normalized: list[SectionCompareItem] = []

    for item in items:
        item = _backfill_policy_ids(item, hits_by_section=hits_by_section)
        if item.section_id not in batch_section_ids:
            if warnings is not None:
                warnings.append(
                    f"section compare dropped unknown section_id {item.section_id!r}"
                )
            continue
        section_text = section_text_by_id.get(item.section_id, "")
        policy_text = _resolve_policy_text(
            item,
            hits_by_section=hits_by_section,
            hit_lookup=hit_lookup,
        )
        if item.status in (ComplianceStatus.COMPLIANT, ComplianceStatus.NON_COMPLIANT) and section_text:
            item = _normalize_item_quotes(
                item,
                section_text=section_text,
                policy_text=policy_text,
                quote_stats=quote_stats,
                anchor_enabled=anchor_enabled,
            )
        if not item.dimension_label:
            item = item.model_copy(update={"dimension_label": item.section_id})
        normalized.append(item)
    return normalized


async def _invoke_compare_batch(
    sections: list[IndexedChunk],
    hits_by_section: dict[str, list[RetrievalHit]],
    *,
    contract_type: str | None,
    memory_context: str,
    extra_user_context: str,
    cfg: ReviewSettings,
    playbook_hints_by_document: dict[str, PlaybookHints] | None,
) -> BatchSectionCompareLLMResult:
    max_chars = cfg.section_compare_max_section_chars
    system_tpl, user_tpl = _load_prompt_template()
    sections_block, _truncated_ids = _format_sections_block(
        sections,
        hits_by_section,
        max_section_chars=max_chars,
        playbook_hints_by_document=playbook_hints_by_document,
        enrich_playbook=cfg.playbook_enrich_compare,
    )

    memory_block = ""
    if memory_context.strip():
        memory_block = f"\n\nPrior review context:\n{memory_context.strip()[:4000]}\n"

    user = user_tpl.format(
        contract_type=(contract_type or "unknown").strip() or "unknown",
        sections_block=sections_block + memory_block,
    )
    if extra_user_context.strip():
        user += "\n\n" + extra_user_context.strip()
    model = get_review_model(
        temperature=cfg.compliance_llm_temperature,
        max_tokens=cfg.compliance_llm_max_tokens,
    )
    return await invoke_structured(
        model,
        BatchSectionCompareLLMResult,
        system=system_tpl,
        user=user,
    )


async def compare_section_batch(
    sections: list[IndexedChunk],
    hits_by_section: dict[str, list[RetrievalHit]],
    *,
    contract_type: str | None = None,
    memory_context: str = "",
    extra_user_context: str = "",
    settings: ReviewSettings | None = None,
    playbook_hints_by_document: dict[str, PlaybookHints] | None = None,
    quote_stats: dict[str, int] | None = None,
    related_by_section: dict | None = None,
) -> tuple[list[SectionCompareItem], list[str]]:
    cfg = settings or get_settings()
    if not sections:
        return [], []

    related_context = extra_user_context
    if related_by_section:
        from review_agent.services.section_cross_reference import format_compare_related_block

        batch_bundles = {
            s.section_id: related_by_section[s.section_id]
            for s in sections
            if s.section_id in related_by_section
        }
        block = format_compare_related_block(
            batch_bundles,
            max_total_chars=cfg.section_compare_context_max_chars,
        )
        if block:
            related_context = f"{related_context}\n\n{block}".strip() if related_context else block

    warnings: list[str] = []
    max_chars = cfg.section_compare_max_section_chars
    _, truncated_ids = _format_sections_block(
        sections,
        hits_by_section,
        max_section_chars=max_chars,
        playbook_hints_by_document=playbook_hints_by_document,
        enrich_playbook=cfg.playbook_enrich_compare,
    )
    if truncated_ids:
        unique = sorted(set(truncated_ids))
        warnings.append(
            f"section compare truncated at {max_chars} chars for: {', '.join(unique)}"
        )

    try:
        result = await _invoke_compare_batch(
            sections,
            hits_by_section,
            contract_type=contract_type,
            memory_context=memory_context,
            extra_user_context=related_context,
            cfg=cfg,
            playbook_hints_by_document=playbook_hints_by_document,
        )
    except FatalPipelineError:
        raise
    except LLMUnavailableError as exc:
        logger.warning("section compare LLM unavailable: %s", exc)
        return _failure_items(sections, reason=str(exc)), warnings
    except Exception as exc:  # noqa: BLE001
        logger.warning("section compare LLM failed: %s", exc)
        if cfg.compare_batch_retry_single and len(sections) > 1:
            retry_items: list[SectionCompareItem] = []
            for section in sections:
                sid = section.section_id
                single_hits = {sid: hits_by_section.get(sid, [])}
                try:
                    single_result = await _invoke_compare_batch(
                        [section],
                        single_hits,
                        contract_type=contract_type,
                        memory_context=memory_context,
                        extra_user_context=related_context,
                        cfg=cfg,
                        playbook_hints_by_document=playbook_hints_by_document,
                    )
                    retry_items.extend(single_result.items)
                except FatalPipelineError:
                    raise
                except Exception as single_exc:  # noqa: BLE001
                    logger.warning(
                        "section compare single retry failed for %s: %s",
                        sid,
                        single_exc,
                    )
                    retry_items.extend(
                        _failure_items([section], reason=str(single_exc))
                    )
            normalized = _postprocess_compare_items(
                retry_items,
                sections,
                hits_by_section,
                quote_stats=quote_stats,
                anchor_enabled=cfg.compare_quote_anchor_enabled,
                warnings=warnings,
            )
            return normalized, warnings
        return _failure_items(sections, reason=str(exc)), warnings

    normalized = _postprocess_compare_items(
        result.items,
        sections,
        hits_by_section,
        quote_stats=quote_stats,
        anchor_enabled=cfg.compare_quote_anchor_enabled,
        warnings=warnings,
    )
    return normalized, warnings


async def compare_all_sections(
    sections: list[IndexedChunk],
    bundles: dict[str, list[RetrievalHit]],
    *,
    contract_type: str | None = None,
    memory_context: str = "",
    settings: ReviewSettings | None = None,
    playbook_hints_by_document: dict[str, PlaybookHints] | None = None,
    categories_by_section: dict[str, list[str]] | None = None,
    related_by_section: dict | None = None,
) -> tuple[list[SectionCompareItem], list[str], dict[str, int | float | str]]:
    cfg = settings or get_settings()
    hits_by_section = {s.section_id: bundles.get(s.section_id, []) for s in sections}
    titles_by_section = {s.section_id: (s.title or s.section_id) for s in sections}
    filtered_hits, hit_selection_stats = filter_hits_for_compare(
        hits_by_section,
        categories_by_section,
        section_titles_by_id=titles_by_section,
        settings=cfg,
    )

    quote_stats: dict[str, int] = {}
    batches = split_batch_by_token_budget(
        sections,
        batch_size=cfg.section_compare_batch_size,
        max_tokens=cfg.section_compare_max_tokens,
        bundles=filtered_hits,
    )

    async def run_batch(batch: list[IndexedChunk]):
        batch_hits = {s.section_id: filtered_hits.get(s.section_id, []) for s in batch}
        return await compare_section_batch(
            batch,
            batch_hits,
            contract_type=contract_type,
            memory_context=memory_context,
            settings=cfg,
            playbook_hints_by_document=playbook_hints_by_document,
            quote_stats=quote_stats,
            related_by_section=related_by_section,
        )

    results = await gather_limited(
        [run_batch(batch) for batch in batches],
        limit=cfg.section_compare_concurrency,
    )

    all_items: list[SectionCompareItem] = []
    all_warnings: list[str] = []
    failed_batches = 0
    for batch, result in zip(batches, results, strict=True):
        if isinstance(result, BaseException):
            failed_batches += 1
            logger.warning("compare batch failed: %s", result)
            all_items.extend(_failure_items(batch, reason=str(result)))
            continue
        items, warnings = result
        all_items.extend(items)
        all_warnings.extend(warnings)

    stats: dict[str, int | float | str] = {
        "llm_batches_actual": len(batches),
        "llm_batches_failed": failed_batches,
        "sections_truncated": len({w for w in all_warnings if "truncated" in w}),
        "compare_quote_anchored": quote_stats.get("compare_quote_anchored", 0),
        "compare_hit_selection": hit_selection_stats,
    }
    return all_items, all_warnings, stats
