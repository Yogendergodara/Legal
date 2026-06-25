"""Obligation extraction graph node (Phase R1)."""

from __future__ import annotations

from typing import Any

from review_agent.clients.document_client import DocumentMCPClient
from review_agent.config import get_settings
from review_agent.services.obligation_extract import extract_obligations_batch
from review_agent.services.routing_tenant import obligation_routing_active
from review_agent.services.section_filter import filter_review_sections
from review_agent.state.review_state import ReviewState


async def obligation_extract_node(
    state: ReviewState,
    client: DocumentMCPClient,
) -> dict[str, Any]:
    _ = client
    settings = get_settings()
    if not settings.obligation_extract_enabled:
        return {}

    sections = filter_review_sections(
        state.get("contract_sections") or [],
        min_chars=settings.review_min_section_chars,
    )
    if not sections:
        return {}

    result = await extract_obligations_batch(sections, settings=settings)
    obligations = list(result.obligations)
    warnings = list(result.warnings)
    if (
        obligation_routing_active(state["tenant_id"], settings)
        and settings.max_obligations_per_review > 0
        and len(obligations) > settings.max_obligations_per_review
    ):
        obligations = obligations[: settings.max_obligations_per_review]
        warnings.append(
            f"obligation list truncated to {settings.max_obligations_per_review} for routing cost control"
        )
    obligation_payload = [item.model_dump(mode="json") for item in obligations]
    boilerplate_count = sum(1 for item in obligations if item.is_boilerplate)
    section_count = len({item.section_id for item in obligations}) or len(sections)
    stats = {
        "obligation_count": len(obligations),
        "boilerplate_obligation_count": boilerplate_count,
        "obligations_per_section_avg": round(len(obligations) / section_count, 2),
    }
    compliance_stats = dict(state.get("compliance_stats") or {})
    compliance_stats.update(stats)

    updates: dict[str, Any] = {
        "obligations": obligation_payload,
        "obligation_extract_stats": stats,
        "compliance_stats": compliance_stats,
    }
    if warnings:
        updates["warnings"] = warnings
    return updates
