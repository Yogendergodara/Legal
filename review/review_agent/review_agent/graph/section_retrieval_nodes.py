"""Phase 10 section policy retrieval graph node."""

from __future__ import annotations

from typing import Any

from review_agent.clients.document_client import DocumentMCPClient
from review_agent.config import get_settings
from review_agent.schemas.section_retrieval import SectionRetrievalBundle
from review_agent.services.async_limits import gather_limited
from review_agent.services.multi_retrieval import multi_retrieve_for_section
from review_agent.services.section_filter import filter_review_sections
from review_agent.state.review_state import ReviewState


async def section_policy_retrieval_node(
    state: ReviewState,
    client: DocumentMCPClient,
) -> dict[str, Any]:
    settings = get_settings()
    sections = filter_review_sections(
        state.get("contract_sections") or [],
        min_chars=settings.review_min_section_chars,
    )

    coros = [
        multi_retrieve_for_section(
            client,
            tenant_id=state["tenant_id"],
            section=section,
            contract_type=state.get("contract_type"),
            policy_type=state.get("policy_type"),
            settings=settings,
        )
        for section in sections
    ]
    results = await gather_limited(coros, limit=settings.section_retrieval_concurrency)

    bundles: dict[str, SectionRetrievalBundle] = {}
    warnings: list[str] = []
    for section, result in zip(sections, results, strict=True):
        if isinstance(result, BaseException):
            warnings.append(f"retrieval failed for section {section.section_id}: {result}")
            bundles[section.section_id] = SectionRetrievalBundle(
                section_id=section.section_id,
                categories=["general"],
                policy_hits=[],
                retrieval_meta={"error": str(result)},
            )
            continue
        bundles[section.section_id] = result

    serialized = {k: v.model_dump(mode="json") for k, v in bundles.items()}
    return {
        "section_retrieval_by_id": serialized,
        "section_review_sections": [s.model_dump(mode="json") for s in sections],
        "warnings": warnings,
    }
