"""LLM-assisted quote repair before MCP verbatim verification (P2-7)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from review_agent.config import ReviewSettings, get_settings
from review_agent.models.llm_gateway import get_review_model, invoke_structured
from review_agent.resilience.failure_policy import (
    ReviewPosture,
    get_current_review_posture,
    is_rate_limited,
)
from review_agent.schemas.quote_repair import (
    BatchQuoteRepairLLMResult,
    QuoteRepairResult,
)
from review_agent.services.quote_validate import quote_is_substring, truncate_section

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "quote_repair.md"


@dataclass(frozen=True)
class QuoteRepairJob:
    repair_id: str
    section_id: str
    source_text: str
    candidate_quote: str


def _load_prompt_template() -> tuple[str, str]:
    raw = _PROMPT_PATH.read_text(encoding="utf-8")
    if "## SYSTEM" not in raw or "## USER" not in raw:
        raise ValueError("quote_repair.md must contain ## SYSTEM and ## USER")
    _, system_block = raw.split("## SYSTEM", 1)
    system_text, user_block = system_block.split("## USER", 1)
    return system_text.strip(), user_block.strip()


def _validate_repair_result(
    result: QuoteRepairResult,
    *,
    source_text: str,
) -> QuoteRepairResult:
    repaired = (result.repaired_quote or "").strip()
    if repaired and not quote_is_substring(repaired, source_text):
        return QuoteRepairResult(
            repaired_quote="",
            repair_notes="LLM returned non-substring; rejected",
        )
    return result.model_copy(update={"repaired_quote": repaired})


def _quota_skip_result() -> QuoteRepairResult:
    return QuoteRepairResult(repair_notes="quote repair skipped: rate limited")


def _skip_batch_jobs(
    batch: list[QuoteRepairJob],
    out: dict[str, QuoteRepairResult],
    stats: dict[str, int] | None,
) -> None:
    for job in batch:
        out[job.repair_id] = _quota_skip_result()
    if stats is not None:
        stats["quote_repair_quota_skipped"] = stats.get("quote_repair_quota_skipped", 0) + len(batch)


async def repair_quote_for_section(
    *,
    source_text: str,
    candidate_quote: str,
    section_id: str,
    settings: ReviewSettings | None = None,
) -> QuoteRepairResult:
    """Select a verbatim substring from source_text matching candidate_quote."""
    candidate = (candidate_quote or "").strip()
    if not candidate or not (source_text or "").strip():
        return QuoteRepairResult(repair_notes="empty input")

    cfg = settings or get_settings()
    if not cfg.quote_repair_enabled:
        return QuoteRepairResult(repair_notes="quote repair disabled")

    system_tpl, user_tpl = _load_prompt_template()
    truncated = truncate_section(source_text, cfg.quote_repair_max_chars)
    user = user_tpl.format(
        section_id=section_id,
        source_text=truncated,
        candidate_quote=candidate,
    )
    model = get_review_model(
        temperature=cfg.compliance_llm_temperature,
        max_tokens=cfg.quote_repair_max_tokens,
    )
    try:
        result = await invoke_structured(
            model,
            QuoteRepairResult,
            system=system_tpl,
            user=user,
        )
    except Exception as exc:  # noqa: BLE001
        if is_rate_limited(exc):
            return _quota_skip_result()
        raise
    return _validate_repair_result(result, source_text=source_text)


async def repair_quotes_batch(
    jobs: list[QuoteRepairJob],
    *,
    settings: ReviewSettings | None = None,
    stats: dict[str, int] | None = None,
) -> dict[str, QuoteRepairResult]:
    """Repair multiple quotes in one or more batched LLM calls."""
    cfg = settings or get_settings()
    if not jobs:
        return {}
    if not cfg.quote_repair_enabled:
        return {
            job.repair_id: QuoteRepairResult(repair_notes="quote repair disabled")
            for job in jobs
        }

    use_batch = cfg.quote_repair_batch_enabled
    batch_size = max(1, cfg.quote_repair_batch_size)
    out: dict[str, QuoteRepairResult] = {}
    batch_calls = 0

    async def _repair_single(job: QuoteRepairJob) -> None:
        out[job.repair_id] = await repair_quote_for_section(
            source_text=job.source_text,
            candidate_quote=job.candidate_quote,
            section_id=job.section_id,
            settings=cfg,
        )

    if not use_batch:
        for job in jobs:
            await _repair_single(job)
        if stats is not None:
            stats["quote_repair_batch_calls"] = stats.get("quote_repair_batch_calls", 0)
        return out

    system_tpl, user_tpl = _load_prompt_template()
    model = get_review_model(
        temperature=cfg.compliance_llm_temperature,
        max_tokens=max(cfg.quote_repair_max_tokens, batch_size * 128),
    )

    for start in range(0, len(jobs), batch_size):
        batch = jobs[start : start + batch_size]
        if len(batch) == 1:
            await _repair_single(batch[0])
            continue

        batch_calls += 1
        blocks: list[str] = []
        for job in batch:
            truncated = truncate_section(job.source_text, cfg.quote_repair_max_chars)
            blocks.append(
                f"### repair_id: {job.repair_id}\n"
                f"section_id: {job.section_id}\n"
                f"source_section:\n```\n{truncated}\n```\n"
                f"candidate_quote:\n```\n{job.candidate_quote}\n```"
            )
        batch_user = (
            "Repair each quote below independently. "
            "Return exactly one result per repair_id.\n\n"
            + "\n\n---\n\n".join(blocks)
        )
        try:
            result = await invoke_structured(
                model,
                BatchQuoteRepairLLMResult,
                system=system_tpl,
                user=batch_user,
            )
        except Exception as exc:  # noqa: BLE001
            if is_rate_limited(exc) or get_current_review_posture() != ReviewPosture.NORMAL:
                _skip_batch_jobs(batch, out, stats)
                continue
            for job in batch:
                await _repair_single(job)
            continue

        expected = {job.repair_id for job in batch}
        source_by_id = {job.repair_id: job.source_text for job in batch}
        returned: set[str] = set()
        for item in result.items:
            if item.repair_id not in expected:
                continue
            returned.add(item.repair_id)
            validated = _validate_repair_result(
                QuoteRepairResult(
                    repaired_quote=item.repaired_quote,
                    confidence=item.confidence,
                    repair_notes=item.repair_notes,
                ),
                source_text=source_by_id[item.repair_id],
            )
            out[item.repair_id] = validated

        for job in batch:
            if job.repair_id not in returned:
                await _repair_single(job)

    if stats is not None:
        stats["quote_repair_batch_calls"] = stats.get("quote_repair_batch_calls", 0) + batch_calls
    return out
