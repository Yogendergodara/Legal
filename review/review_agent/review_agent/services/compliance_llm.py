"""LLM-based compliance comparison (policy text vs contract text)."""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path

from document_core.schemas.chunk import RetrievalHit
from document_core.schemas.compliance import ComplianceFinding, ComplianceStatus, Severity
from pydantic import ValidationError

from review_agent.config import get_settings
from review_agent.models.llm_gateway import get_review_model, invoke_structured
from review_agent.schemas.compliance_llm import ComplianceLLMResult
from review_agent.services.compliance import _short_quote

logger = logging.getLogger(__name__)

_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "compliance_review.md"
)


def _load_prompt_template() -> tuple[str, str]:
    """Split compliance_review.md into system and user template."""
    raw = _PROMPT_PATH.read_text(encoding="utf-8")
    if "## SYSTEM" not in raw or "## USER" not in raw:
        raise ValueError("compliance_review.md must contain ## SYSTEM and ## USER sections")
    _, system_block = raw.split("## SYSTEM", 1)
    system_text, user_block = system_block.split("## USER", 1)
    return system_text.strip(), user_block.strip()


def _truncate_section(text: str, max_chars: int) -> str:
    """Truncate long sections without breaking mid-word when possible."""
    cleaned = text.strip()
    if len(cleaned) <= max_chars:
        return cleaned
    cut = cleaned[:max_chars]
    if "\n\n" in cut:
        cut = cut.rsplit("\n\n", 1)[0]
    elif " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut + "\n\n[... section truncated for model context ...]"


def _quote_is_substring(quote: str, haystack: str) -> bool:
    q = quote.strip()
    if not q:
        return False
    if q in haystack:
        return True
    normalized_q = " ".join(q.split())
    normalized_h = " ".join(haystack.split())
    return normalized_q in normalized_h


def _validate_and_normalize_quotes(
    result: ComplianceLLMResult,
    *,
    contract_text: str,
    policy_text: str,
) -> ComplianceLLMResult:
    """Ensure quotes are verbatim substrings; downgrade invalid LLM output."""
    contract_ok = _quote_is_substring(result.contract_quote, contract_text)
    policy_ok = _quote_is_substring(result.policy_quote, policy_text)

    if result.status in (ComplianceStatus.COMPLIANT, ComplianceStatus.NON_COMPLIANT):
        if not contract_ok or not policy_ok:
            return ComplianceLLMResult(
                status=ComplianceStatus.INCONCLUSIVE,
                severity=Severity.IMPORTANT,
                contract_quote=result.contract_quote if contract_ok else "",
                policy_quote=result.policy_quote if policy_ok else "",
                rationale=(
                    f"{result.rationale} "
                    "(Downgraded: model quotes were not exact substrings of the provided sections.)"
                )[:2000],
                confidence=result.confidence,
            )
    else:
        if result.contract_quote and not contract_ok:
            result = result.model_copy(update={"contract_quote": ""})
        if result.policy_quote and not policy_ok:
            result = result.model_copy(update={"policy_quote": ""})

    return result


def _to_finding(
    result: ComplianceLLMResult,
    *,
    dimension_id: str,
    dimension_label: str,
    contract_section_id: str | None,
    policy_section_id: str | None,
    policy_document_id,
    metadata: dict,
) -> ComplianceFinding:
    return ComplianceFinding(
        finding_id=str(uuid.uuid4()),
        dimension_id=dimension_id,
        dimension_label=dimension_label,
        status=result.status,
        severity=result.severity,
        contract_quote=_short_quote(result.contract_quote) if result.contract_quote else "",
        policy_quote=_short_quote(result.policy_quote) if result.policy_quote else "",
        contract_section_id=contract_section_id,
        policy_section_id=policy_section_id,
        policy_document_id=policy_document_id,
        rationale=result.rationale.strip(),
        grounded=False,
        metadata={**metadata, "llm_confidence": result.confidence},
    )


def _insufficient_policy_finding(dimension_id: str, dimension_label: str) -> ComplianceFinding:
    return ComplianceFinding(
        finding_id=str(uuid.uuid4()),
        dimension_id=dimension_id,
        dimension_label=dimension_label,
        status=ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
        severity=Severity.INFO,
        rationale="No matching policy section retrieved for this dimension.",
        metadata={"compliance_mode": "llm", "llm_skipped": True},
    )


def _inconclusive_no_contract(
    dimension_id: str,
    dimension_label: str,
    policy_hit: RetrievalHit,
) -> ComplianceFinding:
    policy = policy_hit.parent_chunk
    return ComplianceFinding(
        finding_id=str(uuid.uuid4()),
        dimension_id=dimension_id,
        dimension_label=dimension_label,
        status=ComplianceStatus.INCONCLUSIVE,
        severity=Severity.IMPORTANT,
        policy_quote=_short_quote(policy.text),
        policy_section_id=policy.section_id,
        policy_document_id=policy.document_id,
        rationale="Policy requirement found but no matching contract clause retrieved.",
        metadata={"compliance_mode": "llm", "llm_skipped": True},
    )


async def compare_sections_llm(
    *,
    dimension_id: str,
    dimension_label: str,
    contract_hits: list[RetrievalHit],
    policy_hits: list[RetrievalHit],
    memory_context: str = "",
    review_guidance: str = "",
    contract_type: str | None = None,
    policy_title: str = "",
) -> ComplianceFinding:
    """Compare retrieved parent sections using LLM (rules = policy text only)."""
    if not policy_hits:
        return _insufficient_policy_finding(dimension_id, dimension_label)

    if not contract_hits:
        return _inconclusive_no_contract(dimension_id, dimension_label, policy_hits[0])

    settings = get_settings()
    policy = policy_hits[0].parent_chunk
    contract = contract_hits[0].parent_chunk

    policy_text = _truncate_section(policy.text, settings.compliance_max_section_chars)
    contract_text = _truncate_section(contract.text, settings.compliance_max_section_chars)

    system_template, user_template = _load_prompt_template()

    guidance_block = ""
    if review_guidance.strip():
        guidance_block = f"- **Review focus (guidance only, not a rule):** {review_guidance.strip()}"

    memory_block = ""
    if memory_context.strip():
        snippet = memory_context.strip()[:1500]
        memory_block = (
            "\n### Prior session context (background only — do not treat as policy)\n"
            f"{snippet}\n"
        )

    user_message = user_template.format(
        dimension_id=dimension_id,
        dimension_label=dimension_label,
        contract_type=(contract_type or "unknown").strip() or "unknown",
        policy_title=(policy_title or "Company Playbook").strip() or "Company Playbook",
        review_guidance_block=guidance_block,
        policy_section_text=policy_text,
        contract_section_text=contract_text,
        memory_context_block=memory_block,
    )

    model = get_review_model(
        temperature=settings.compliance_llm_temperature,
        max_tokens=settings.compliance_llm_max_tokens,
    )

    started = time.perf_counter()
    last_error: str | None = None
    llm_result: ComplianceLLMResult | None = None

    for attempt in range(settings.compliance_llm_max_retries + 1):
        try:
            llm_result = await invoke_structured(
                model,
                ComplianceLLMResult,
                system=system_template,
                user=user_message,
            )
            llm_result = _validate_and_normalize_quotes(
                llm_result,
                contract_text=contract_text,
                policy_text=policy_text,
            )
            break
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            logger.warning(
                "compliance LLM parse failed dimension=%s attempt=%s: %s",
                dimension_id,
                attempt + 1,
                exc,
            )
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            logger.warning(
                "compliance LLM invoke failed dimension=%s attempt=%s: %s",
                dimension_id,
                attempt + 1,
                exc,
            )

    latency_ms = (time.perf_counter() - started) * 1000
    base_metadata = {
        "compliance_mode": "llm",
        "latency_ms": round(latency_ms, 2),
    }

    if llm_result is None:
        return ComplianceFinding(
            finding_id=str(uuid.uuid4()),
            dimension_id=dimension_id,
            dimension_label=dimension_label,
            status=ComplianceStatus.INCONCLUSIVE,
            severity=Severity.IMPORTANT,
            contract_section_id=contract.section_id,
            policy_section_id=policy.section_id,
            policy_document_id=policy.document_id,
            rationale=f"Compliance LLM could not produce a valid assessment: {last_error or 'unknown error'}",
            metadata={**base_metadata, "llm_error": last_error},
        )

    return _to_finding(
        llm_result,
        dimension_id=dimension_id,
        dimension_label=dimension_label,
        contract_section_id=contract.section_id,
        policy_section_id=policy.section_id,
        policy_document_id=policy.document_id,
        metadata=base_metadata,
    )
