"""Section-first LLM compliance compare (Phase 10B)."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from uuid import UUID

from document_core.schemas.chunk import IndexedChunk, RetrievalHit
from document_core.schemas.compliance import ComplianceStatus
from review_agent.config import ReviewSettings, get_settings
from review_agent.models.llm_gateway import get_review_model, invoke_structured
from review_agent.schemas.section_compare import BatchSectionCompareLLMResult, SectionCompareItem
from review_agent.services.compliance_llm import _truncate_section, _validate_and_normalize_quotes
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


def _format_sections_block(
    sections: list[IndexedChunk],
    hits_by_section: dict[str, list[RetrievalHit]],
    *,
    max_section_chars: int,
) -> str:
    blocks: list[str] = []
    for section in sections:
        blocks.append(f"### Contract section: {section.section_id} — {section.title}")
        blocks.append(
            f"```\n{_truncate_section(section.text or '', max_section_chars)}\n```"
        )
        policy_hits = hits_by_section.get(section.section_id) or []
        if not policy_hits:
            blocks.append("- **Policies:** [none retrieved]")
            continue
        for idx, hit in enumerate(policy_hits, start=1):
            parent = hit.parent_chunk
            blocks.append(
                f"- **Policy {idx}** doc={parent.document_id} section={parent.section_id} "
                f"title={parent.title}\n```\n"
                f"{_truncate_section(parent.text or '', max_section_chars)}\n```"
            )
    return "\n\n".join(blocks)


def _normalize_item_quotes(
    item: SectionCompareItem,
    *,
    section_text: str,
    policy_text: str,
) -> SectionCompareItem:
    from review_agent.schemas.compliance_llm import ComplianceLLMResult

    adapted = ComplianceLLMResult(
        status=item.status,
        severity=item.severity,
        contract_quote=item.contract_quote,
        policy_quote=item.policy_quote,
        rationale=item.rationale,
        confidence=item.confidence,
    )
    normalized = _validate_and_normalize_quotes(
        adapted,
        contract_text=section_text,
        policy_text=policy_text,
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


async def compare_section_batch(
    sections: list[IndexedChunk],
    hits_by_section: dict[str, list[RetrievalHit]],
    *,
    contract_type: str | None = None,
    settings: ReviewSettings | None = None,
) -> list[SectionCompareItem]:
    cfg = settings or get_settings()
    if not sections:
        return []

    system_tpl, user_tpl = _load_prompt_template()
    sections_block = _format_sections_block(
        sections,
        hits_by_section,
        max_section_chars=cfg.compliance_max_section_chars,
    )
    user = user_tpl.format(
        contract_type=(contract_type or "unknown").strip() or "unknown",
        sections_block=sections_block,
    )
    model = get_review_model(
        temperature=cfg.compliance_llm_temperature,
        max_tokens=cfg.compliance_llm_max_tokens,
    )
    try:
        result = await invoke_structured(
            model,
            BatchSectionCompareLLMResult,
            system=system_tpl,
            user=user,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("section compare LLM failed: %s", exc)
        return []

    section_text_by_id = {s.section_id: s.text or "" for s in sections}
    policy_text_by_key: dict[str, str] = {}
    for sid, hits in hits_by_section.items():
        for hit in hits:
            key = f"{sid}:{hit.parent_chunk.document_id}:{hit.parent_chunk.section_id}"
            policy_text_by_key[key] = hit.parent_chunk.text or ""

    normalized: list[SectionCompareItem] = []
    for item in result.items:
        section_text = section_text_by_id.get(item.section_id, "")
        policy_key = f"{item.section_id}:{item.policy_document_id}:{item.policy_section_id}"
        policy_text = policy_text_by_key.get(policy_key, "")
        if item.status in (ComplianceStatus.COMPLIANT, ComplianceStatus.NON_COMPLIANT) and policy_text:
            item = _normalize_item_quotes(
                item,
                section_text=section_text,
                policy_text=policy_text,
            )
        if not item.dimension_label:
            item = item.model_copy(update={"dimension_label": item.section_id})
        normalized.append(item)
    return normalized


async def compare_all_sections(
    sections: list[IndexedChunk],
    bundles: dict[str, list[RetrievalHit]],
    *,
    contract_type: str | None = None,
    settings: ReviewSettings | None = None,
) -> list[SectionCompareItem]:
    cfg = settings or get_settings()
    hits_by_section = {s.section_id: bundles.get(s.section_id, []) for s in sections}
    batches = split_batch_by_token_budget(
        sections,
        batch_size=cfg.section_compare_batch_size,
        max_tokens=cfg.section_compare_max_tokens,
        bundles=hits_by_section,
    )
    all_items: list[SectionCompareItem] = []
    for batch in batches:
        items = await compare_section_batch(
            batch,
            hits_by_section,
            contract_type=contract_type,
            settings=cfg,
        )
        all_items.extend(items)
    return all_items
