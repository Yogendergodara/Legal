"""Extract contract obligations from sections (Phase R1)."""

from __future__ import annotations

import logging
from pathlib import Path

from document_core.schemas.chunk import IndexedChunk
from review_agent.config import ReviewSettings, get_settings
from review_agent.models.llm_gateway import get_review_model, invoke_structured
from review_agent.schemas.obligation import (
    BatchObligationExtractResult,
    ContractObligation,
    ObligationExtractResult,
    SectionObligationExtractResult,
)
from review_agent.services.named_policy_routing import extract_named_policy_title_keys
from review_agent.services.obligation_boilerplate import (
    infer_obligation_boilerplate,
    section_title_is_boilerplate,
)

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "obligation_extract.md"


def _split_prompt(raw: str) -> tuple[str, str]:
    parts = raw.split("## USER", 1)
    system = parts[0].replace("## SYSTEM", "").strip()
    user = parts[1].strip() if len(parts) > 1 else ""
    return system, user


def _fallback_obligations(section: IndexedChunk) -> list[ContractObligation]:
    body = (section.text or "").strip()
    if not body:
        return []
    title = section.title or section.section_id
    boilerplate = section_title_is_boilerplate(title)
    mentions = extract_named_policy_title_keys(body)
    return [
        ContractObligation(
            obligation_id=f"{section.section_id}-o0",
            section_id=section.section_id,
            text=body,
            char_start=0,
            char_end=len(body),
            obligation_type="boilerplate" if boilerplate else "general",
            is_boilerplate=boilerplate,
            explicit_policy_mentions=mentions,
            extract_source="fallback",
        )
    ]


def _finalize_obligation(
    section: IndexedChunk,
    *,
    index: int,
    text: str,
    obligation_type: str,
    explicit_policy_mentions: list[str],
    extract_source: str,
) -> ContractObligation:
    body = section.text or ""
    start = body.find(text) if text else -1
    if start < 0:
        start = 0
        span_text = body
    else:
        span_text = text
    end = start + len(span_text)
    title = section.title or section.section_id
    mentions = list(explicit_policy_mentions) or extract_named_policy_title_keys(span_text)
    boilerplate = infer_obligation_boilerplate(
        text=span_text,
        section_title=title,
        obligation_type=obligation_type,
    ) or section_title_is_boilerplate(title)
    return ContractObligation(
        obligation_id=f"{section.section_id}-o{index}",
        section_id=section.section_id,
        text=span_text,
        char_start=start,
        char_end=end,
        obligation_type=obligation_type or ("boilerplate" if boilerplate else "general"),
        is_boilerplate=boilerplate,
        explicit_policy_mentions=mentions,
        extract_source=extract_source,  # type: ignore[arg-type]
    )


def _sections_user_block(sections: list[IndexedChunk], max_chars: int) -> str:
    blocks: list[str] = []
    for section in sections:
        body = (section.text or "")[:max_chars]
        blocks.append(
            f"### Section {section.section_id}\n"
            f"Title: {section.title or section.section_id}\n"
            f"Body:\n{body}\n"
        )
    return "\n".join(blocks)


async def extract_obligations_batch(
    sections: list[IndexedChunk],
    *,
    settings: ReviewSettings | None = None,
) -> ObligationExtractResult:
    cfg = settings or get_settings()
    if not sections:
        return ObligationExtractResult()

    warnings: list[str] = []
    obligations: list[ContractObligation] = []
    template = _PROMPT_PATH.read_text(encoding="utf-8")
    system_tpl, _ = _split_prompt(template)

    for start in range(0, len(sections), cfg.obligation_extract_batch_size):
        batch = sections[start : start + cfg.obligation_extract_batch_size]
        try:
            user = _sections_user_block(batch, cfg.obligation_extract_max_section_chars)
            user += (
                "\n\nReturn JSON: {\"sections\": [{\"section_id\": \"...\", "
                "\"obligations\": [{\"index\": 0, \"text\": \"...\", "
                "\"obligation_type\": \"...\", \"explicit_policy_mentions\": []}]}]}"
            )
            model = get_review_model(
                temperature=cfg.compliance_llm_temperature,
                max_tokens=cfg.compliance_llm_max_tokens,
            )
            result = await invoke_structured(
                model,
                BatchObligationExtractResult,
                system=system_tpl,
                user=user,
            )
            by_id = {section.section_id: section for section in batch}
            for item in result.sections:
                section = by_id.get(item.section_id)
                if section is None:
                    continue
                if not item.obligations:
                    obligations.extend(_fallback_obligations(section))
                    continue
                for ob in item.obligations:
                    obligations.append(
                        _finalize_obligation(
                            section,
                            index=ob.index,
                            text=(ob.text or "").strip(),
                            obligation_type=(ob.obligation_type or "").strip(),
                            explicit_policy_mentions=list(ob.explicit_policy_mentions or []),
                            extract_source="llm",
                        )
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning("obligation extract LLM failed for batch: %s", exc)
            warnings.append(f"obligation extract LLM failed: {exc}")
            for section in batch:
                obligations.extend(_fallback_obligations(section))

    if not obligations:
        for section in sections:
            obligations.extend(_fallback_obligations(section))

    return ObligationExtractResult(obligations=obligations, warnings=warnings)
