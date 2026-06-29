"""LLM rationale rewrite when guard returns UNSUPPORTED (P2-6)."""

from __future__ import annotations

from pathlib import Path

from document_core.schemas.compliance import ComplianceFinding
from review_agent.config import ReviewSettings, get_settings
from review_agent.models.llm_gateway import get_review_model, invoke_structured
from review_agent.schemas.guard_llm import (
    BatchRationaleRepairLLMResult,
    RationaleRepairResult,
)

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "rationale_repair.md"
_QUOTE_CAP = 800


def _load_prompt_template() -> tuple[str, str]:
    raw = _PROMPT_PATH.read_text(encoding="utf-8")
    if "## SYSTEM" not in raw or "## USER" not in raw:
        raise ValueError("rationale_repair.md must contain ## SYSTEM and ## USER")
    _, system_block = raw.split("## SYSTEM", 1)
    system_text, user_block = system_block.split("## USER", 1)
    return system_text.strip(), user_block.strip()


def _truncate(text: str, cap: int = _QUOTE_CAP) -> str:
    cleaned = (text or "").strip()
    return cleaned if len(cleaned) <= cap else cleaned[: cap - 3] + "..."


def _format_repair_user_block(finding: ComplianceFinding, user_tpl: str) -> str:
    return user_tpl.format(
        status=finding.status.value,
        dimension_label=finding.dimension_label or "",
        contract_quote=_truncate(finding.contract_quote),
        policy_quote=_truncate(finding.policy_quote),
        rationale=(finding.rationale or "")[:2000],
    )


async def repair_rationale_for_finding(
    finding: ComplianceFinding,
    *,
    settings: ReviewSettings | None = None,
) -> str:
    cfg = settings or get_settings()
    system_tpl, user_tpl = _load_prompt_template()
    user = _format_repair_user_block(finding, user_tpl)
    model = get_review_model(
        temperature=cfg.compliance_llm_temperature,
        max_tokens=cfg.guard_pass_max_tokens,
    )
    result = await invoke_structured(
        model,
        RationaleRepairResult,
        system=system_tpl,
        user=user,
    )
    return result.rationale.strip()


async def repair_rationales_batch(
    findings: list[ComplianceFinding],
    *,
    settings: ReviewSettings | None = None,
) -> dict[str, str]:
    """Rewrite rationales for multiple findings in one or more batched LLM calls."""
    cfg = settings or get_settings()
    if not findings:
        return {}

    use_batch = cfg.guard_rationale_repair_batch_enabled
    batch_size = max(1, cfg.guard_pass_batch_size)

    async def _repair_single(finding: ComplianceFinding) -> tuple[str, str]:
        text = await repair_rationale_for_finding(finding, settings=cfg)
        return finding.finding_id, text

    if not use_batch or len(findings) == 1:
        out: dict[str, str] = {}
        for finding in findings:
            fid, text = await _repair_single(finding)
            out[fid] = text
        return out

    system_tpl, user_tpl = _load_prompt_template()
    model = get_review_model(
        temperature=cfg.compliance_llm_temperature,
        max_tokens=max(cfg.guard_pass_max_tokens, batch_size * 400),
    )
    out = {}
    for start in range(0, len(findings), batch_size):
        batch = findings[start : start + batch_size]
        if len(batch) == 1:
            fid, text = await _repair_single(batch[0])
            out[fid] = text
            continue

        blocks = [
            f"### finding_id: {finding.finding_id}\n"
            f"{_format_repair_user_block(finding, user_tpl)}"
            for finding in batch
        ]
        batch_user = (
            "Rewrite each rationale below independently. "
            "Return exactly one result per finding_id.\n\n"
            + "\n\n---\n\n".join(blocks)
        )
        try:
            result = await invoke_structured(
                model,
                BatchRationaleRepairLLMResult,
                system=system_tpl,
                user=batch_user,
            )
        except Exception:  # noqa: BLE001
            for finding in batch:
                fid, text = await _repair_single(finding)
                out[fid] = text
            continue

        expected = {f.finding_id for f in batch}
        returned: set[str] = set()
        for item in result.items:
            if item.finding_id not in expected:
                continue
            returned.add(item.finding_id)
            out[item.finding_id] = item.rationale.strip()

        for finding in batch:
            if finding.finding_id not in returned:
                fid, text = await _repair_single(finding)
                out[fid] = text

    return out
