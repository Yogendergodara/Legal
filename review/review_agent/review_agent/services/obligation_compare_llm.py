"""Obligation-first LLM compliance compare (Phase R6)."""

from __future__ import annotations

import logging
import math
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
from review_agent.services.compare_failure_status import classify_compare_failure
from review_agent.resilience.failure_policy import (
    get_current_review_posture,
    note_batch_llm_failure,
    should_batch_single_retry,
)
from review_agent.services.equivalence_guard import apply_equivalence_guard
from review_agent.services.playbook_context import PlaybookHints, format_playbook_hint_block, hints_from_chunk_metadata
from review_agent.services.quote_validate import truncate_section, validate_and_normalize_quotes
from review_agent.services.token_budget import (
    effective_compare_max_tokens,
    split_obligations_by_token_budget,
)

from review_agent.services.compare_prompt_loader import obligation_compare_prompt_path

logger = logging.getLogger(__name__)


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
    preserve_non_compliant_on_quote_fail: bool = False,
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
        preserve_non_compliant_on_quote_fail=preserve_non_compliant_on_quote_fail,
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


def _backfill_obligation_policy_ids(
    item: ObligationCompareItem,
    hits: list[RetrievalHit],
) -> tuple[ObligationCompareItem, bool]:
    if item.policy_document_id or not hits:
        return item, False
    if item.policy_section_id:
        for hit in hits:
            if hit.parent_chunk.section_id == item.policy_section_id:
                return item.model_copy(
                    update={"policy_document_id": str(hit.parent_chunk.document_id)}
                ), True
    if len(hits) == 1:
        parent = hits[0].parent_chunk
        return item.model_copy(
            update={
                "policy_document_id": str(parent.document_id),
                "policy_section_id": parent.section_id,
            }
        ), True
    best = max(hits, key=lambda h: h.score)
    parent = best.parent_chunk
    return item.model_copy(
        update={
            "policy_document_id": str(parent.document_id),
            "policy_section_id": parent.section_id,
        }
    ), True


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
    template = obligation_compare_prompt_path(cfg).read_text(encoding="utf-8")
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


async def _compare_obligation_batch_with_retry(
    batch: list[ContractObligation],
    hits_by_obligation: dict[str, list[RetrievalHit]],
    *,
    contract_type: str | None,
    memory_context: str,
    cfg: ReviewSettings,
    playbook_hints_by_document: dict[str, PlaybookHints] | None,
) -> tuple[BatchObligationCompareLLMResult, bool]:
    batch_hits = {ob.obligation_id: hits_by_obligation.get(ob.obligation_id, []) for ob in batch}
    try:
        result = await _invoke_compare_batch(
            batch,
            batch_hits,
            contract_type=contract_type,
            memory_context=memory_context,
            cfg=cfg,
            playbook_hints_by_document=playbook_hints_by_document,
        )
        return result, False
    except FatalPipelineError:
        raise
    except LLMUnavailableError:
        raise
    except Exception as exc:
        note_batch_llm_failure()
        if not should_batch_single_retry(
            exc,
            batch_len=len(batch),
            batch_retry_enabled=cfg.compare_batch_retry_single,
            posture_enabled=cfg.llm_review_posture_enabled,
        ):
            merged: list[ObligationCompareItem] = []
            posture = get_current_review_posture().value
            for ob in batch:
                single_hits = {ob.obligation_id: batch_hits.get(ob.obligation_id, [])}
                merged.append(
                    ObligationCompareItem(
                        obligation_id=ob.obligation_id,
                        section_id=ob.section_id,
                        dimension_label=ob.obligation_type or ob.section_id,
                        status=classify_compare_failure(
                            str(exc),
                            has_policy_evidence=bool(single_hits.get(ob.obligation_id)),
                            transient_inconclusive=cfg.compare_failure_transient_inconclusive,
                            obligation_section_cutover_mode=cfg.obligation_section_cutover_mode,
                            llm_review_posture=posture,
                        ),
                        severity=Severity.INFO,
                        rationale=f"Obligation compare failed: {exc}"[:2000],
                    )
                )
            return BatchObligationCompareLLMResult(items=merged), False
        logger.warning("obligation compare batch failed, retrying single: %s", exc)
        merged: list[ObligationCompareItem] = []
        for ob in batch:
            single_hits = {ob.obligation_id: batch_hits.get(ob.obligation_id, [])}
            try:
                single = await _invoke_compare_batch(
                    [ob],
                    single_hits,
                    contract_type=contract_type,
                    memory_context=memory_context,
                    cfg=cfg,
                    playbook_hints_by_document=playbook_hints_by_document,
                )
                merged.extend(single.items)
            except FatalPipelineError:
                raise
            except Exception as single_exc:  # noqa: BLE001
                logger.warning(
                    "obligation compare single retry failed for %s: %s",
                    ob.obligation_id,
                    single_exc,
                )
                merged.append(
                    ObligationCompareItem(
                        obligation_id=ob.obligation_id,
                        section_id=ob.section_id,
                        dimension_label=ob.obligation_type or ob.section_id,
                        status=classify_compare_failure(
                            str(single_exc),
                            has_policy_evidence=bool(single_hits.get(ob.obligation_id)),
                            transient_inconclusive=cfg.compare_failure_transient_inconclusive,
                            obligation_section_cutover_mode=cfg.obligation_section_cutover_mode,
                            llm_review_posture=get_current_review_posture().value,
                        ),
                        severity=Severity.INFO,
                        rationale=f"Obligation compare single retry failed: {single_exc}"[:2000],
                    )
                )
        return BatchObligationCompareLLMResult(items=merged), True


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
    max_tokens = effective_compare_max_tokens(cfg.obligation_compare_max_tokens, cfg)
    batches = split_obligations_by_token_budget(
        compare_queue,
        batch_size=cfg.obligation_compare_batch_size,
        max_tokens=max_tokens,
        hits_by_obligation=hits_by_obligation,
        settings=cfg,
    )
    config_max = (
        math.ceil(len(compare_queue) / cfg.obligation_compare_batch_size)
        if compare_queue and cfg.obligation_compare_batch_size
        else 0
    )

    single_retry_batches = 0
    single_recovered = 0

    async def run_batch(batch: list[ContractObligation]):
        nonlocal single_retry_batches, single_recovered
        batch_hits = {ob.obligation_id: hits_by_obligation.get(ob.obligation_id, []) for ob in batch}
        result, retried = await _compare_obligation_batch_with_retry(
            batch,
            batch_hits,
            contract_type=contract_type,
            memory_context=memory_context,
            cfg=cfg,
            playbook_hints_by_document=playbook_hints_by_document,
        )
        if retried:
            single_retry_batches += 1
            single_recovered += len(result.items)
        return result

    results = await gather_limited(
        [run_batch(batch) for batch in batches],
        limit=cfg.section_compare_concurrency,
    )

    text_by_obligation = {ob.obligation_id: ob.text or "" for ob in compare_queue}
    items: list[ObligationCompareItem] = []
    failed_batches = 0
    omitted_count = 0
    backfill_count = 0
    transient_failures = 0

    for batch, result in zip(batches, results, strict=True):
        if isinstance(result, BaseException):
            failed_batches += 1
            logger.warning("obligation compare batch failed: %s", result)
            for ob in batch:
                has_hits = bool(hits_by_obligation.get(ob.obligation_id))
                status = classify_compare_failure(
                    str(result),
                    has_policy_evidence=has_hits,
                    transient_inconclusive=cfg.compare_failure_transient_inconclusive,
                    obligation_section_cutover_mode=cfg.obligation_section_cutover_mode,
                    llm_review_posture=get_current_review_posture().value,
                )
                if status == ComplianceStatus.INCONCLUSIVE:
                    transient_failures += 1
                items.append(
                    ObligationCompareItem(
                        obligation_id=ob.obligation_id,
                        section_id=ob.section_id,
                        dimension_label=ob.obligation_type or ob.section_id,
                        status=status,
                        severity=Severity.INFO,
                        rationale=f"Obligation compare failed: {result}"[:2000],
                    )
                )
            continue

        known_ids = {ob.obligation_id for ob in batch}
        returned_ids: set[str] = set()
        for raw in result.items:
            if raw.obligation_id not in known_ids:
                warnings.append(f"obligation compare dropped unknown id {raw.obligation_id!r}")
                continue
            returned_ids.add(raw.obligation_id)
            hits = hits_by_obligation.get(raw.obligation_id, [])
            item, backfilled = _backfill_obligation_policy_ids(raw, hits)
            if backfilled:
                backfill_count += 1
            if item.status in (ComplianceStatus.COMPLIANT, ComplianceStatus.NON_COMPLIANT):
                item = _normalize_item(
                    item,
                    obligation_text=text_by_obligation.get(raw.obligation_id, ""),
                    policy_text=_resolve_policy_text(item, hits),
                    anchor_enabled=cfg.compare_quote_anchor_enabled,
                    preserve_non_compliant_on_quote_fail=(
                        cfg.grounding_downgrade_mode == "keep_status_flag"
                    ),
                )
            if not item.dimension_label:
                item = item.model_copy(update={"dimension_label": item.obligation_id})
            items.append(item)

        for ob in batch:
            if ob.obligation_id in returned_ids:
                continue
            omitted_count += 1
            warnings.append(f"obligation compare omitted {ob.obligation_id} from batch response")
            items.append(
                ObligationCompareItem(
                    obligation_id=ob.obligation_id,
                    section_id=ob.section_id,
                    dimension_label=ob.obligation_type or ob.section_id,
                    status=ComplianceStatus.INCONCLUSIVE,
                    severity=Severity.INFO,
                    rationale="Obligation compare omitted from LLM batch response",
                )
            )

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
        "obligation_llm_batches_config_max": config_max,
        "obligation_llm_batches_token_limited": max(0, len(batches) - config_max),
        "obligation_compare_omitted": omitted_count,
        "obligation_compare_single_retries": single_retry_batches,
        "obligation_compare_single_recovered": single_recovered,
        "obligation_compare_policy_id_backfilled": backfill_count,
        "obligation_compare_transient_failure_count": transient_failures,
    }
    return items, warnings, stats
