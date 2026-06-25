"""Obligation-first LLM compliance compare (Phase R6)."""

from __future__ import annotations

import logging
from pathlib import Path

from document_core.schemas.chunk import RetrievalHit
from document_core.schemas.compliance import ComplianceStatus, Severity
from review_agent.config import ReviewSettings, get_settings
from review_agent.errors import FatalPipelineError, LLMUnavailableError
from review_agent.models.llm_gateway import get_review_model, invoke_structured
from review_agent.schemas.compliance_llm import ComplianceLLMResult
from review_agent.schemas.evidence_sufficiency import EvidenceSufficiencyResult
from review_agent.schemas.obligation import ContractObligation
from review_agent.schemas.obligation_compare import (
    BatchObligationCompareLLMResult,
    ObligationCompareItem,
)
from review_agent.schemas.routing_plan import CatalogMatchResult, ObligationRoutingPlan
from review_agent.schemas.section_compare import SectionCompareItem
from review_agent.services.async_limits import gather_limited
from review_agent.services.equivalence_guard import apply_equivalence_guard
from review_agent.services.playbook_context import PlaybookHints, format_playbook_hint_block, hints_from_chunk_metadata
from review_agent.services.quote_validate import truncate_section, validate_and_normalize_quotes

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "obligation_compare.md"


def _split_prompt(raw: str) -> tuple[str, str]:
    parts = raw.split("## USER", 1)
    system = parts[0].replace("## SYSTEM", "").strip()
    user = parts[1].strip() if len(parts) > 1 else ""
    return system, user


def ipc_item_from_evidence(
    obligation: ContractObligation,
    evidence: EvidenceSufficiencyResult,
    *,
    plan: ObligationRoutingPlan,
    match: CatalogMatchResult,
) -> ObligationCompareItem:
    reason = evidence.reason or match.route_decision
    return ObligationCompareItem(
        obligation_id=obligation.obligation_id,
        section_id=obligation.section_id,
        dimension_label=obligation.obligation_type or obligation.section_id,
        status=ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
        severity=Severity.INFO,
        contract_quote="",
        policy_quote="",
        rationale=(
            f"Obligation compare skipped ({evidence.decision}): {reason}. "
            f"Routing confidence={plan.confidence:.2f}, source={plan.routing_source}."
        ),
        confidence=plan.confidence,
    )


def _format_obligations_block(
    obligations: list[ContractObligation],
    hits_by_obligation: dict[str, list[RetrievalHit]],
    *,
    max_chars: int,
    playbook_hints_by_document: dict[str, PlaybookHints] | None,
    enrich_playbook: bool,
) -> str:
    blocks: list[str] = []
    hints_map = playbook_hints_by_document or {}
    for ob in obligations:
        body = truncate_section(ob.text or "", max_chars)
        blocks.append(
            f"### Obligation: {ob.obligation_id} (section {ob.section_id})\n"
            f"Type: {ob.obligation_type or 'general'}\n"
            f"```\n{body}\n```"
        )
        hits = hits_by_obligation.get(ob.obligation_id) or []
        if not hits:
            blocks.append("- **Policies:** [none retrieved]")
            continue
        for idx, hit in enumerate(hits[:4], start=1):
            parent = hit.parent_chunk
            blocks.append(
                f"- **Policy {idx}** doc={parent.document_id} section={parent.section_id} "
                f"title={parent.title}"
            )
            if enrich_playbook:
                hints = hints_map.get(str(parent.document_id)) or hints_from_chunk_metadata(
                    parent.metadata
                )
                hint_block = format_playbook_hint_block(hints)
                if hint_block:
                    blocks.append(hint_block)
            blocks.append(f"```\n{truncate_section(parent.text or '', max_chars)}\n```")
    return "\n\n".join(blocks)


def _normalize_item(
    item: ObligationCompareItem,
    *,
    obligation_text: str,
    policy_text: str,
    anchor_enabled: bool,
) -> ObligationCompareItem:
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
        contract_text=obligation_text,
        policy_text=policy_text,
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


def _resolve_policy_text(
    item: ObligationCompareItem,
    hits: list[RetrievalHit],
) -> str:
    for hit in hits:
        parent = hit.parent_chunk
        if item.policy_document_id and str(parent.document_id) != item.policy_document_id:
            continue
        if item.policy_section_id and parent.section_id != item.policy_section_id:
            continue
        return parent.text or ""
    if hits:
        return hits[0].parent_chunk.text or ""
    return ""


def _to_section_shim(item: ObligationCompareItem) -> SectionCompareItem:
    return SectionCompareItem(
        section_id=item.section_id,
        policy_document_id=item.policy_document_id,
        policy_section_id=item.policy_section_id,
        dimension_label=item.dimension_label,
        status=item.status,
        severity=item.severity,
        contract_quote=item.contract_quote,
        policy_quote=item.policy_quote,
        rationale=item.rationale,
        confidence=item.confidence,
    )


def _from_section_shim(item: SectionCompareItem, obligation_id: str) -> ObligationCompareItem:
    return ObligationCompareItem(
        obligation_id=obligation_id,
        section_id=item.section_id,
        policy_document_id=item.policy_document_id,
        policy_section_id=item.policy_section_id,
        dimension_label=item.dimension_label,
        status=item.status,
        severity=item.severity,
        contract_quote=item.contract_quote,
        policy_quote=item.policy_quote,
        rationale=item.rationale,
        confidence=item.confidence,
    )


async def _invoke_compare_batch(
    obligations: list[ContractObligation],
    hits_by_obligation: dict[str, list[RetrievalHit]],
    *,
    contract_type: str | None,
    memory_context: str,
    cfg: ReviewSettings,
    playbook_hints_by_document: dict[str, PlaybookHints] | None,
) -> BatchObligationCompareLLMResult:
    template = _PROMPT_PATH.read_text(encoding="utf-8")
    system_tpl, user_tpl = _split_prompt(template)
    block = _format_obligations_block(
        obligations,
        hits_by_obligation,
        max_chars=cfg.obligation_compare_max_obligation_chars,
        playbook_hints_by_document=playbook_hints_by_document,
        enrich_playbook=cfg.playbook_enrich_compare,
    )
    memory_block = ""
    if memory_context.strip():
        memory_block = f"\n\nPrior review context:\n{memory_context.strip()[:4000]}\n"
    user = user_tpl.format(
        contract_type=(contract_type or "unknown").strip() or "unknown",
        obligations_block=block + memory_block,
    )
    model = get_review_model(
        temperature=cfg.compliance_llm_temperature,
        max_tokens=cfg.compliance_llm_max_tokens,
    )
    return await invoke_structured(
        model,
        BatchObligationCompareLLMResult,
        system=system_tpl,
        user=user,
    )


async def compare_obligations_batch(
    compare_queue: list[ContractObligation],
    evidence_by_id: dict[str, EvidenceSufficiencyResult],
    hits_by_obligation: dict[str, list[RetrievalHit]],
    *,
    contract_type: str | None = None,
    memory_context: str = "",
    settings: ReviewSettings | None = None,
    playbook_hints_by_document: dict[str, PlaybookHints] | None = None,
    sections_by_id: dict | None = None,
) -> tuple[list[ObligationCompareItem], list[str], dict[str, int]]:
    cfg = settings or get_settings()
    if not compare_queue:
        return [], [], {"obligation_compare_llm_batches": 0}

    warnings: list[str] = []
    batches: list[list[ContractObligation]] = []
    for start in range(0, len(compare_queue), cfg.obligation_compare_batch_size):
        batches.append(compare_queue[start : start + cfg.obligation_compare_batch_size])

    async def run_batch(batch: list[ContractObligation]):
        batch_hits = {ob.obligation_id: hits_by_obligation.get(ob.obligation_id, []) for ob in batch}
        return await _invoke_compare_batch(
            batch,
            batch_hits,
            contract_type=contract_type,
            memory_context=memory_context,
            cfg=cfg,
            playbook_hints_by_document=playbook_hints_by_document,
        )

    results = await gather_limited(
        [run_batch(batch) for batch in batches],
        limit=cfg.section_compare_concurrency,
    )

    text_by_obligation = {ob.obligation_id: ob.text or "" for ob in compare_queue}
    items: list[ObligationCompareItem] = []
    failed_batches = 0

    for batch, result in zip(batches, results, strict=True):
        if isinstance(result, BaseException):
            failed_batches += 1
            logger.warning("obligation compare batch failed: %s", result)
            for ob in batch:
                items.append(
                    ObligationCompareItem(
                        obligation_id=ob.obligation_id,
                        section_id=ob.section_id,
                        dimension_label=ob.obligation_type or ob.section_id,
                        status=ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
                        severity=Severity.INFO,
                        rationale=f"Obligation compare failed: {result}"[:2000],
                    )
                )
            continue

        known_ids = {ob.obligation_id for ob in batch}
        for raw in result.items:
            if raw.obligation_id not in known_ids:
                warnings.append(f"obligation compare dropped unknown id {raw.obligation_id!r}")
                continue
            hits = hits_by_obligation.get(raw.obligation_id, [])
            item = raw
            if item.status in (ComplianceStatus.COMPLIANT, ComplianceStatus.NON_COMPLIANT):
                item = _normalize_item(
                    item,
                    obligation_text=text_by_obligation.get(raw.obligation_id, ""),
                    policy_text=_resolve_policy_text(item, hits),
                    anchor_enabled=cfg.compare_quote_anchor_enabled,
                )
            if not item.dimension_label:
                item = item.model_copy(update={"dimension_label": item.obligation_id})
            items.append(item)

    if cfg.incorporation_guard_enabled and sections_by_id:
        from review_agent.services.incorporation_guard import apply_incorporation_guard

        shims = [_to_section_shim(item) for item in items]
        guarded, _ = apply_incorporation_guard(shims, sections_by_id)
        items = [
            _from_section_shim(guarded_item, original.obligation_id)
            for original, guarded_item in zip(items, guarded, strict=True)
        ]

    shims = [_to_section_shim(item) for item in items]
    guarded, _ = apply_equivalence_guard(shims)
    items = [
        _from_section_shim(guarded_item, original.obligation_id)
        for original, guarded_item in zip(items, guarded, strict=True)
    ]

    stats = {
        "obligation_compare_llm_batches": len(batches),
        "obligation_compare_llm_batches_failed": failed_batches,
    }
    return items, warnings, stats
