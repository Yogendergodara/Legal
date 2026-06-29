"""Post-grounding rationale guard — tiered LLM quote→rationale support (P2-6 / P1 batch)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Literal

from document_core.schemas.compliance import ComplianceFinding, ComplianceStatus, Severity
from review_agent.config import ReviewSettings, get_settings
from review_agent.models.llm_gateway import get_review_model, invoke_structured
from review_agent.schemas.guard_llm import (
    BatchRationaleGuardLLMResult,
    RationaleGuardResult,
    SupportLevel,
)
from review_agent.services.rationale_repair_llm import (
    repair_rationale_for_finding,
    repair_rationales_batch,
)
from review_agent.resilience.failure_policy import ReviewPosture, get_current_review_posture

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "rationale_guard.md"
_QUOTE_CAP = 800

GuardOutcomeKind = Literal["skipped", "checked", "inference_ok", "repaired", "failed"]


def _load_prompt_template() -> tuple[str, str]:
    raw = _PROMPT_PATH.read_text(encoding="utf-8")
    if "## SYSTEM" not in raw or "## USER" not in raw:
        raise ValueError("rationale_guard.md must contain ## SYSTEM and ## USER")
    _, system_block = raw.split("## SYSTEM", 1)
    system_text, user_block = system_block.split("## USER", 1)
    return system_text.strip(), user_block.strip()


def _should_guard(finding: ComplianceFinding, settings: ReviewSettings) -> bool:
    meta = finding.metadata or {}
    if meta.get("guard_failed"):
        return False

    prior_nc = meta.get("prior_status") == ComplianceStatus.NON_COMPLIANT.value
    ungrounded_nc_kept = (
        finding.status == ComplianceStatus.NON_COMPLIANT
        and meta.get("grounding_failed") is True
    )
    downgraded_from_nc = (
        finding.status == ComplianceStatus.INCONCLUSIVE
        and prior_nc
        and meta.get("grounding_failed") is True
    )

    if settings.guard_pass_non_compliant_only:
        if finding.status == ComplianceStatus.NON_COMPLIANT:
            if not ungrounded_nc_kept and finding.grounded is not True:
                return False
        elif finding.status == ComplianceStatus.INCONCLUSIVE:
            if not downgraded_from_nc:
                return False
        else:
            return False
    elif finding.status not in (
        ComplianceStatus.COMPLIANT,
        ComplianceStatus.NON_COMPLIANT,
    ):
        if not downgraded_from_nc:
            return False
    elif meta.get("grounding_failed") and not ungrounded_nc_kept:
        return False

    if not (ungrounded_nc_kept or downgraded_from_nc) and finding.grounded is not True:
        return False
    if not (finding.contract_quote or finding.policy_quote):
        return False
    if not (finding.rationale or "").strip():
        return False
    return True


def _truncate(text: str, cap: int = _QUOTE_CAP) -> str:
    cleaned = (text or "").strip()
    return cleaned if len(cleaned) <= cap else cleaned[: cap - 3] + "..."


def _playbook_guidance(finding: ComplianceFinding) -> str:
    meta = finding.metadata or {}
    guidance = meta.get("review_guidance") or meta.get("playbook_guidance") or ""
    return str(guidance).strip() or "(none)"


def _format_finding_user_block(finding: ComplianceFinding, user_tpl: str) -> str:
    return user_tpl.format(
        status=finding.status.value,
        dimension_label=finding.dimension_label or "",
        playbook_guidance=_playbook_guidance(finding),
        contract_quote=_truncate(finding.contract_quote),
        policy_quote=_truncate(finding.policy_quote),
        rationale=(finding.rationale or "")[:2000],
    ).strip()


async def _invoke_guard(
    finding: ComplianceFinding,
    *,
    system_tpl: str,
    user_tpl: str,
    model: Any,
) -> RationaleGuardResult:
    user = _format_finding_user_block(finding, user_tpl)
    return await invoke_structured(
        model,
        RationaleGuardResult,
        system=system_tpl,
        user=user,
    )


async def _invoke_guard_batch(
    findings: list[ComplianceFinding],
    *,
    system_tpl: str,
    user_tpl: str,
    model: Any,
) -> dict[str, RationaleGuardResult]:
    blocks: list[str] = []
    for finding in findings:
        blocks.append(
            f"### finding_id: {finding.finding_id}\n"
            f"{_format_finding_user_block(finding, user_tpl)}"
        )
    batch_user = (
        "Verify each finding below independently. "
        "Return exactly one result per finding_id.\n\n"
        + "\n\n---\n\n".join(blocks)
    )
    result = await invoke_structured(
        model,
        BatchRationaleGuardLLMResult,
        system=system_tpl,
        user=batch_user,
    )
    expected = {f.finding_id for f in findings}
    out: dict[str, RationaleGuardResult] = {}
    for item in result.items:
        if item.finding_id not in expected:
            continue
        out[item.finding_id] = RationaleGuardResult(
            support_level=item.support_level,
            reason=item.reason,
        )
    return out


def _apply_support_level(
    finding: ComplianceFinding,
    result: RationaleGuardResult,
) -> tuple[ComplianceFinding, Literal["checked", "inference_ok"]]:
    meta = dict(finding.metadata or {})
    meta["guard_support_level"] = result.support_level.value
    if result.reason:
        meta["guard_reason"] = result.reason[:500]
    kind: Literal["checked", "inference_ok"] = (
        "inference_ok" if result.support_level == SupportLevel.INFERENCE_OK else "checked"
    )
    return finding.model_copy(update={"metadata": meta}), kind


def _downgrade_finding(
    finding: ComplianceFinding,
    *,
    reason: str = "",
) -> ComplianceFinding:
    meta = dict(finding.metadata or {})
    meta["guard_failed"] = True
    meta["prior_status"] = finding.status.value
    if reason:
        meta["guard_reason"] = reason[:500]
    return finding.model_copy(
        update={
            "status": ComplianceStatus.INCONCLUSIVE,
            "severity": Severity.IMPORTANT,
            "grounded": False,
            "metadata": meta,
        }
    )


async def _process_guard_result(
    finding: ComplianceFinding,
    result: RationaleGuardResult,
    *,
    system_tpl: str,
    user_tpl: str,
    model: Any,
    settings: ReviewSettings,
) -> tuple[ComplianceFinding, GuardOutcomeKind]:
    if result.support_level in (SupportLevel.FULL, SupportLevel.INFERENCE_OK):
        updated, kind = _apply_support_level(finding, result)
        return updated, kind

    if settings.guard_rationale_repair_enabled:
        if settings.llm_review_posture_enabled and get_current_review_posture() in (
            ReviewPosture.HOT,
            ReviewPosture.DEGRADED,
        ):
            meta = dict(finding.metadata or {})
            meta["guard_deferred"] = True
            if result.reason:
                meta["guard_reason"] = result.reason[:500]
            return _downgrade_finding(
                finding.model_copy(update={"metadata": meta}),
                reason=result.reason,
            ), "failed"

        meta = dict(finding.metadata or {})
        meta["guard_repair_attempted"] = True
        if result.reason:
            meta["guard_reason"] = result.reason[:500]
        try:
            repaired_text = await repair_rationale_for_finding(
                finding,
                settings=settings,
            )
        except Exception:
            return _downgrade_finding(finding, reason=result.reason), "failed"

        repaired_finding = finding.model_copy(
            update={"rationale": repaired_text, "metadata": meta},
        )
        result2 = await _invoke_guard(
            repaired_finding,
            system_tpl=system_tpl,
            user_tpl=user_tpl,
            model=model,
        )
        if result2.support_level in (SupportLevel.FULL, SupportLevel.INFERENCE_OK):
            meta2 = dict(repaired_finding.metadata or {})
            meta2["guard_repair_success"] = True
            meta2["guard_support_level"] = result2.support_level.value
            if result2.reason:
                meta2["guard_reason"] = result2.reason[:500]
            return repaired_finding.model_copy(update={"metadata": meta2}), "repaired"

        return _downgrade_finding(repaired_finding, reason=result2.reason), "failed"

    return _downgrade_finding(finding, reason=result.reason), "failed"


async def guard_finding(
    finding: ComplianceFinding,
    *,
    system_tpl: str,
    user_tpl: str,
    model: Any,
    settings: ReviewSettings,
) -> tuple[ComplianceFinding, GuardOutcomeKind]:
    if not _should_guard(finding, settings):
        return finding, "skipped"

    result = await _invoke_guard(
        finding,
        system_tpl=system_tpl,
        user_tpl=user_tpl,
        model=model,
    )
    return await _process_guard_result(
        finding,
        result,
        system_tpl=system_tpl,
        user_tpl=user_tpl,
        model=model,
        settings=settings,
    )


def _record_outcome(
    stats: dict[str, int],
    warnings: list[str],
    *,
    finding: ComplianceFinding,
    updated: ComplianceFinding,
    kind: GuardOutcomeKind,
) -> None:
    if kind == "skipped":
        stats["guard_skipped"] += 1
        return
    if kind in ("checked", "inference_ok", "repaired"):
        stats["guard_checked"] += 1
        if kind == "inference_ok":
            stats["guard_inference_ok"] += 1
        elif kind == "repaired":
            stats["guard_repair_attempts"] += 1
            stats["guard_repair_success"] += 1
            if updated.metadata.get("guard_support_level") == SupportLevel.INFERENCE_OK.value:
                stats["guard_inference_ok"] += 1
        return
    if kind == "failed":
        stats["guard_checked"] += 1
        stats["guard_failed"] += 1
        meta = updated.metadata or {}
        if meta.get("guard_repair_attempted"):
            stats["guard_repair_attempts"] += 1
        warnings.append(
            f"finding downgraded to INCONCLUSIVE (guard failed): {finding.dimension_label}"
        )


async def _guard_batch(
    batch: list[ComplianceFinding],
    *,
    system_tpl: str,
    user_tpl: str,
    model: Any,
    settings: ReviewSettings,
) -> dict[str, tuple[ComplianceFinding, GuardOutcomeKind]]:
    outcomes: dict[str, tuple[ComplianceFinding, GuardOutcomeKind]] = {}
    try:
        results_map = await _invoke_guard_batch(
            batch,
            system_tpl=system_tpl,
            user_tpl=user_tpl,
            model=model,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("batch guard failed, falling back per finding: %s", exc)
        if settings.llm_review_posture_enabled and get_current_review_posture() in (
            ReviewPosture.HOT,
            ReviewPosture.DEGRADED,
        ):
            for finding in batch:
                meta = dict(finding.metadata or {})
                meta["guard_deferred"] = True
                outcomes[finding.finding_id] = (
                    finding.model_copy(update={"metadata": meta}),
                    "skipped",
                )
            return outcomes
        for finding in batch:
            outcomes[finding.finding_id] = await guard_finding(
                finding,
                system_tpl=system_tpl,
                user_tpl=user_tpl,
                model=model,
                settings=settings,
            )
        return outcomes

    for finding in batch:
        result = results_map.get(finding.finding_id)
        if result is None:
            logger.warning(
                "batch guard omitted finding_id %s, falling back to single",
                finding.finding_id,
            )
            outcomes[finding.finding_id] = await guard_finding(
                finding,
                system_tpl=system_tpl,
                user_tpl=user_tpl,
                model=model,
                settings=settings,
            )
            continue
        if result.support_level in (SupportLevel.FULL, SupportLevel.INFERENCE_OK):
            outcomes[finding.finding_id] = _apply_support_level(finding, result)
            continue
        if settings.llm_review_posture_enabled and get_current_review_posture() in (
            ReviewPosture.HOT,
            ReviewPosture.DEGRADED,
        ):
            meta = dict(finding.metadata or {})
            meta["guard_deferred"] = True
            if result.reason:
                meta["guard_reason"] = result.reason[:500]
            outcomes[finding.finding_id] = (
                _downgrade_finding(
                    finding.model_copy(update={"metadata": meta}),
                    reason=result.reason,
                ),
                "failed",
            )
            continue
        outcomes[finding.finding_id] = (finding, result)

    pending: list[tuple[ComplianceFinding, RationaleGuardResult]] = []
    for finding in batch:
        entry = outcomes.get(finding.finding_id)
        if entry is None or not isinstance(entry, tuple) or len(entry) != 2:
            continue
        stored_finding, stored_result = entry
        if stored_finding is finding and isinstance(stored_result, RationaleGuardResult):
            pending.append((finding, stored_result))

    if pending and settings.guard_rationale_repair_enabled:
        repaired_texts = await repair_rationales_batch(
            [finding for finding, _ in pending],
            settings=settings,
        )
        repaired_findings: list[ComplianceFinding] = []
        repair_meta_by_id: dict[str, dict[str, Any]] = {}
        for finding, result in pending:
            meta = dict(finding.metadata or {})
            meta["guard_repair_attempted"] = True
            if result.reason:
                meta["guard_reason"] = result.reason[:500]
            repaired_text = repaired_texts.get(finding.finding_id, finding.rationale or "")
            repaired_findings.append(
                finding.model_copy(update={"rationale": repaired_text, "metadata": meta})
            )
            repair_meta_by_id[finding.finding_id] = meta

        try:
            reguard_map = await _invoke_guard_batch(
                repaired_findings,
                system_tpl=system_tpl,
                user_tpl=user_tpl,
                model=model,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("batch re-guard after repair failed: %s", exc)
            for finding, result in pending:
                outcomes[finding.finding_id] = _downgrade_finding(finding, reason=result.reason), "failed"
            return outcomes

        for repaired_finding in repaired_findings:
            result2 = reguard_map.get(repaired_finding.finding_id)
            orig_result = next(r for f, r in pending if f.finding_id == repaired_finding.finding_id)
            if result2 is None:
                outcomes[repaired_finding.finding_id] = (
                    _downgrade_finding(repaired_finding, reason=orig_result.reason),
                    "failed",
                )
                continue
            if result2.support_level in (SupportLevel.FULL, SupportLevel.INFERENCE_OK):
                meta2 = dict(repair_meta_by_id.get(repaired_finding.finding_id) or {})
                meta2["guard_repair_success"] = True
                meta2["guard_support_level"] = result2.support_level.value
                if result2.reason:
                    meta2["guard_reason"] = result2.reason[:500]
                outcomes[repaired_finding.finding_id] = (
                    repaired_finding.model_copy(update={"metadata": meta2}),
                    "repaired",
                )
            else:
                outcomes[repaired_finding.finding_id] = (
                    _downgrade_finding(repaired_finding, reason=result2.reason),
                    "failed",
                )
    elif pending:
        for finding, result in pending:
            outcomes[finding.finding_id] = await _process_guard_result(
                finding,
                result,
                system_tpl=system_tpl,
                user_tpl=user_tpl,
                model=model,
                settings=settings,
            )

    return outcomes


async def run_guard_pass(
    findings: list[ComplianceFinding],
    *,
    settings: ReviewSettings | None = None,
) -> tuple[list[ComplianceFinding], list[str], dict[str, int]]:
    cfg = settings or get_settings()
    stats: dict[str, int] = {
        "guard_checked": 0,
        "guard_failed": 0,
        "guard_skipped": 0,
        "guard_inference_ok": 0,
        "guard_repair_attempts": 0,
        "guard_repair_success": 0,
        "guard_batch_calls": 0,
    }
    if not cfg.guard_pass_enabled or cfg.guard_pass_mode != "llm":
        stats["guard_skipped"] = len(findings)
        return findings, [], stats

    checkable = [f for f in findings if _should_guard(f, cfg)]
    if not checkable:
        stats["guard_skipped"] = len(findings)
        return findings, [], stats

    system_tpl, user_tpl = _load_prompt_template()
    batch_size = max(1, cfg.guard_pass_batch_size)
    max_tokens = max(cfg.guard_pass_max_tokens, batch_size * 400)
    model = get_review_model(max_tokens=max_tokens)
    sem = asyncio.Semaphore(max(1, cfg.guard_pass_concurrency))
    warnings: list[str] = []

    batches = [
        checkable[i : i + batch_size] for i in range(0, len(checkable), batch_size)
    ]

    async def _run_batch(batch: list[ComplianceFinding]):
        async with sem:
            stats["guard_batch_calls"] += 1
            return await _guard_batch(
                batch,
                system_tpl=system_tpl,
                user_tpl=user_tpl,
                model=model,
                settings=cfg,
            )

    batch_outcomes = await asyncio.gather(*[_run_batch(batch) for batch in batches])
    outcome_by_id: dict[str, tuple[ComplianceFinding, GuardOutcomeKind]] = {}
    for batch_result in batch_outcomes:
        outcome_by_id.update(batch_result)

    guarded: list[ComplianceFinding] = []
    for finding in findings:
        if finding.finding_id in outcome_by_id:
            updated, kind = outcome_by_id[finding.finding_id]
            guarded.append(updated)
            _record_outcome(
                stats,
                warnings,
                finding=finding,
                updated=updated,
                kind=kind,
            )
        else:
            guarded.append(finding)
            stats["guard_skipped"] += 1

    return guarded, warnings, stats
