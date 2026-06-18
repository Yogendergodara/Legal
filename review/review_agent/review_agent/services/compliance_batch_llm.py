"""Batched LLM compliance for hybrid mode (Pass 1 and Pass 2)."""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path

from document_core.schemas.chunk import RetrievalHit
from document_core.schemas.compliance import ComplianceFinding, ComplianceStatus, Severity
from pydantic import ValidationError

from review_agent.config import ReviewSettings, get_settings
from review_agent.models.llm_gateway import get_review_model, invoke_structured
from review_agent.schemas.alignment import AlignmentRecord
from review_agent.schemas.compliance_llm import (
    BatchComplianceItem,
    BatchComplianceLLMResult,
    ComplianceLLMResult,
)
from review_agent.schemas.gap_request import GapRequest
from review_agent.schemas.review_category import ReviewCategory
from review_agent.services.compliance_llm import (
    _short_quote,
    _to_finding,
    _truncate_section,
    _validate_and_normalize_quotes,
)
from review_agent.services.compliance_batch import chunk_categories

logger = logging.getLogger(__name__)

_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "compliance_review_batch.md"
)


def _load_batch_prompt_template() -> tuple[str, str]:
    raw = _PROMPT_PATH.read_text(encoding="utf-8")
    if "## SYSTEM" not in raw or "## USER" not in raw:
        raise ValueError("compliance_review_batch.md must contain ## SYSTEM and ## USER")
    _, system_block = raw.split("## SYSTEM", 1)
    system_text, user_block = system_block.split("## USER", 1)
    return system_text.strip(), user_block.strip()


def _format_batch_items(
    batch: list[ReviewCategory],
    alignment_by_category: dict[str, AlignmentRecord],
    policy_hits_by_category: dict[str, list[RetrievalHit]],
    contract_hits_by_category: dict[str, list[RetrievalHit]],
    policy_titles_by_doc: dict[str, str],
) -> str:
    blocks: list[str] = []
    for category in batch:
        alignment = alignment_by_category.get(category.category_id)
        policy_text = alignment.policy_text_excerpt if alignment else ""
        contract_text = alignment.contract_text_excerpt if alignment else ""
        if not policy_text and policy_hits_by_category.get(category.category_id):
            policy_text = _truncate_section(
                policy_hits_by_category[category.category_id][0].parent_chunk.text,
                get_settings().compliance_max_section_chars,
            )
        if not contract_text and contract_hits_by_category.get(category.category_id):
            contract_text = _truncate_section(
                contract_hits_by_category[category.category_id][0].parent_chunk.text,
                get_settings().compliance_max_section_chars,
            )
        doc_id = str(category.policy_document_id) if category.policy_document_id else ""
        playbook = policy_titles_by_doc.get(doc_id, "Company Playbook")
        blocks.append(
            f"### Item: {category.category_id}\n"
            f"- **Label:** {category.label}\n"
            f"- **Playbook:** {playbook}\n"
            f"- **Policy section:**\n```\n{policy_text or '[no policy text retrieved]'}\n```\n"
            f"- **Contract section:**\n```\n{contract_text or '[no contract section retrieved]'}\n```\n"
        )
    return "\n".join(blocks)


def _item_to_finding(
    item: BatchComplianceItem,
    category: ReviewCategory,
    policy_hits: list[RetrievalHit],
    contract_hits: list[RetrievalHit],
    *,
    compliance_pass: str,
    latency_ms: float,
    retrieval_meta: dict,
) -> ComplianceFinding:
    policy_section_id = None
    policy_document_id = None
    contract_section_id = None
    if policy_hits:
        policy_section_id = policy_hits[0].parent_chunk.section_id
        policy_document_id = policy_hits[0].parent_chunk.document_id
    if contract_hits:
        contract_section_id = contract_hits[0].parent_chunk.section_id

    single = _to_finding(
        _validate_and_normalize_quotes(
            ComplianceLLMResult(
                status=item.status,
                severity=item.severity,
                contract_quote=item.contract_quote,
                policy_quote=item.policy_quote,
                rationale=item.rationale,
                confidence=item.confidence,
            ),
            contract_text=contract_hits[0].parent_chunk.text if contract_hits else "",
            policy_text=policy_hits[0].parent_chunk.text if policy_hits else "",
        ),
        dimension_id=category.category_id,
        dimension_label=category.label,
        contract_section_id=contract_section_id,
        policy_section_id=policy_section_id,
        policy_document_id=policy_document_id,
        metadata={
            **retrieval_meta,
            "compliance_mode": "hybrid",
            "compliance_pass": compliance_pass,
            "latency_ms": round(latency_ms, 2),
            "needs_policy": item.needs_policy,
        },
    )
    return single


def extract_gap_requests(
    items: list[BatchComplianceItem],
    categories_by_id: dict[str, ReviewCategory],
) -> list[dict]:
    gaps: list[dict] = []
    for item in items:
        if not item.needs_policy:
            continue
        category = categories_by_id.get(item.category_id)
        queries = list(item.suggested_search_queries)
        if category and category.search_queries:
            for q in category.search_queries:
                if q not in queries:
                    queries.append(q)
        gaps.append(
            GapRequest(
                category_id=item.category_id,
                policy_topic=item.policy_topic or (category.label if category else ""),
                contract_quote=_short_quote(item.contract_quote),
                suggested_search_queries=queries,
            ).model_dump(mode="json")
        )
    return gaps


async def compare_batch_llm(
    batch: list[ReviewCategory],
    *,
    alignment_by_category: dict[str, AlignmentRecord],
    policy_hits_by_category: dict[str, list[RetrievalHit]],
    contract_hits_by_category: dict[str, list[RetrievalHit]],
    retrieval_meta_by_category: dict[str, dict],
    memory_context: str = "",
    compliance_pass: str = "pass1",
    settings: ReviewSettings | None = None,
    policy_titles_by_doc: dict[str, str] | None = None,
    contract_type: str | None = None,
) -> tuple[list[ComplianceFinding], list[dict]]:
    """Run one batched LLM compare; return findings and gap request dicts."""
    cfg = settings or get_settings()
    titles = policy_titles_by_doc or {}
    if not batch:
        return [], []

    system_template, user_template = _load_batch_prompt_template()
    memory_block = ""
    if memory_context.strip():
        snippet = memory_context.strip()[:1500]
        memory_block = (
            "\n### Prior session context (background only — not policy)\n"
            f"{snippet}\n"
        )

    user_message = user_template.format(
        item_count=len(batch),
        contract_type=(contract_type or "unknown").strip() or "unknown",
        memory_context_block=memory_block,
        batch_items_block=_format_batch_items(
            batch,
            alignment_by_category,
            policy_hits_by_category,
            contract_hits_by_category,
            titles,
        ),
    )

    model = get_review_model(
        temperature=cfg.compliance_llm_temperature,
        max_tokens=cfg.compliance_llm_max_tokens,
    )

    started = time.perf_counter()
    last_error: str | None = None
    batch_result: BatchComplianceLLMResult | None = None

    for attempt in range(cfg.compliance_llm_max_retries + 1):
        try:
            batch_result = await invoke_structured(
                model,
                BatchComplianceLLMResult,
                system=system_template,
                user=user_message,
            )
            break
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            logger.warning("batch compliance parse failed pass=%s attempt=%s: %s", compliance_pass, attempt + 1, exc)
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            logger.warning("batch compliance invoke failed pass=%s attempt=%s: %s", compliance_pass, attempt + 1, exc)

    latency_ms = (time.perf_counter() - started) * 1000
    categories_by_id = {c.category_id: c for c in batch}

    if batch_result is None:
        findings = []
        for category in batch:
            meta = retrieval_meta_by_category.get(category.category_id, {})
            findings.append(
                ComplianceFinding(
                    finding_id=str(uuid.uuid4()),
                    dimension_id=category.category_id,
                    dimension_label=category.label,
                    status=ComplianceStatus.INCONCLUSIVE,
                    severity=Severity.IMPORTANT,
                    rationale=f"Batch compliance LLM failed: {last_error or 'unknown error'}",
                    metadata={
                        **meta,
                        "compliance_mode": "hybrid",
                        "compliance_pass": compliance_pass,
                        "llm_error": last_error,
                    },
                )
            )
        return findings, []

    by_id = {item.category_id: item for item in batch_result.items}
    findings: list[ComplianceFinding] = []
    gap_items: list[BatchComplianceItem] = []

    for category in batch:
        item = by_id.get(category.category_id)
        meta = retrieval_meta_by_category.get(category.category_id, {})
        p_hits = policy_hits_by_category.get(category.category_id, [])
        c_hits = contract_hits_by_category.get(category.category_id, [])

        if item is None:
            findings.append(
                ComplianceFinding(
                    finding_id=str(uuid.uuid4()),
                    dimension_id=category.category_id,
                    dimension_label=category.label,
                    status=ComplianceStatus.INCONCLUSIVE,
                    severity=Severity.IMPORTANT,
                    rationale="Batch LLM omitted this category_id from its response.",
                    metadata={**meta, "compliance_mode": "hybrid", "compliance_pass": compliance_pass},
                )
            )
            continue

        if item.needs_policy:
            gap_items.append(item)
            if not p_hits:
                findings.append(
                    ComplianceFinding(
                        finding_id=str(uuid.uuid4()),
                        dimension_id=category.category_id,
                        dimension_label=category.label,
                        status=ComplianceStatus.INCONCLUSIVE,
                        severity=Severity.IMPORTANT,
                        contract_quote=_short_quote(item.contract_quote),
                        rationale=item.rationale,
                        metadata={**meta, "compliance_mode": "hybrid", "compliance_pass": compliance_pass, "needs_policy": True},
                    )
                )
                continue

        findings.append(
            _item_to_finding(
                item,
                category,
                p_hits,
                c_hits,
                compliance_pass=compliance_pass,
                latency_ms=latency_ms / max(len(batch), 1),
                retrieval_meta=meta,
            )
        )

    return findings, extract_gap_requests(gap_items, categories_by_id)


async def run_batched_compliance(
    categories: list[ReviewCategory],
    *,
    alignment_by_category: dict[str, AlignmentRecord],
    policy_hits_by_category: dict[str, list[RetrievalHit]],
    contract_hits_by_category: dict[str, list[RetrievalHit]],
    retrieval_meta_by_category: dict[str, dict],
    memory_context: str = "",
    compliance_pass: str = "pass1",
    settings: ReviewSettings | None = None,
    policy_titles_by_doc: dict[str, str] | None = None,
    contract_type: str | None = None,
) -> tuple[list[ComplianceFinding], list[dict]]:
    """Split categories into batches and run LLM compares with concurrency limit."""
    from review_agent.services.async_limits import gather_limited

    cfg = settings or get_settings()
    batches = chunk_categories(categories, cfg.compliance_batch_size)
    if not batches:
        return [], []

    async def run_batch(batch: list[ReviewCategory]):
        return await compare_batch_llm(
            batch,
            alignment_by_category=alignment_by_category,
            policy_hits_by_category=policy_hits_by_category,
            contract_hits_by_category=contract_hits_by_category,
            retrieval_meta_by_category=retrieval_meta_by_category,
            memory_context=memory_context,
            compliance_pass=compliance_pass,
            settings=cfg,
            policy_titles_by_doc=policy_titles_by_doc,
            contract_type=contract_type,
        )

    results = await gather_limited(
        [run_batch(batch) for batch in batches],
        limit=cfg.compliance_llm_concurrency,
    )

    all_findings: list[ComplianceFinding] = []
    all_gaps: list[dict] = []
    for result in results:
        if isinstance(result, BaseException):
            logger.warning("batched compliance batch failed: %s", result)
            continue
        findings, gaps = result
        all_findings.extend(findings)
        all_gaps.extend(gaps)
    return all_findings, all_gaps
