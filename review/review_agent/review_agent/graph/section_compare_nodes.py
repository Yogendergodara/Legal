"""Phase 10 section-first compare, merge, and gap verify nodes."""

from __future__ import annotations

from typing import Any

from document_core.schemas.chunk import IndexedChunk, RetrievalHit
from review_agent.clients.document_client import DocumentMCPClient
from review_agent.config import get_settings
from review_agent.schemas.section_retrieval import SectionRetrievalBundle
from review_agent.services.section_compare_llm import compare_all_sections
from review_agent.services.section_merge import merge_section_findings
from review_agent.state.review_state import ReviewState


def _load_bundles(state: ReviewState) -> dict[str, SectionRetrievalBundle]:
    raw = state.get("section_retrieval_by_id") or {}
    return {
        key: SectionRetrievalBundle.model_validate(value)
        for key, value in raw.items()
    }


def _load_sections(state: ReviewState) -> list[IndexedChunk]:
    raw = state.get("section_review_sections") or []
    return [IndexedChunk.model_validate(item) for item in raw]


async def section_compare_llm_node(
    state: ReviewState,
    client: DocumentMCPClient,
) -> dict[str, Any]:
    _ = client
    settings = get_settings()
    sections = _load_sections(state)
    bundles = _load_bundles(state)

    hits_by_section: dict[str, list[RetrievalHit]] = {
        sid: list(bundle.policy_hits) for sid, bundle in bundles.items()
    }

    # Only send sections that have at least one policy hit to compare LLM
    sections_with_policy = [s for s in sections if hits_by_section.get(s.section_id)]
    items = await compare_all_sections(
        sections_with_policy,
        hits_by_section,
        contract_type=state.get("contract_type"),
        settings=settings,
    )

    stats = {
        "compliance_mode": "section_first",
        "sections_total": len(sections),
        "sections_with_policy": len(sections_with_policy),
        "compare_items": len(items),
        "llm_batches_est": max(
            1,
            (len(sections_with_policy) + settings.section_compare_batch_size - 1)
            // settings.section_compare_batch_size,
        ),
    }
    return {
        "section_compare_items": [i.model_dump(mode="json") for i in items],
        "compliance_stats": stats,
    }


async def merge_section_findings_node(
    state: ReviewState,
    client: DocumentMCPClient,
) -> dict[str, Any]:
    _ = client
    from review_agent.schemas.section_compare import SectionCompareItem

    bundles = _load_bundles(state)
    raw_items = state.get("section_compare_items") or []
    items = [SectionCompareItem.model_validate(i) for i in raw_items]
    findings, warnings = merge_section_findings(items, bundles)
    return {"findings": findings, "warnings": warnings}


async def final_gap_verify_node(
    state: ReviewState,
    client: DocumentMCPClient,
) -> dict[str, Any]:
    """Pass-through for v1 — gap findings already merged; extend with LLM later."""
    _ = client
    _ = state
    return {}
