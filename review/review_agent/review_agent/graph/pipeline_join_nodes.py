"""Graph join barriers for parallel hybrid pipeline (PF-1C + PG-3)."""

from __future__ import annotations

from typing import Any

from review_agent.config import get_settings
from review_agent.services.routing_tenant import obligation_routing_active
from review_agent.state.review_state import ReviewState


async def pre_compare_join_node(state: ReviewState) -> dict[str, Any]:
    """Synchronize section retrieval and obligation evidence before compare fan-out."""
    settings = get_settings()
    stats = dict(state.get("compliance_stats") or {})
    stats["review_pipeline_topology"] = settings.review_pipeline_mode
    stats["pipeline_join"] = "pre_compare"

    sections_retrieved = int(stats.get("sections_retrieved") or 0)
    if not sections_retrieved:
        sections_retrieved = len(state.get("section_retrieval_by_id") or {})

    obligations = state.get("obligations") or []
    routing_active = obligation_routing_active(state["tenant_id"], settings)
    compare_ready = int(stats.get("obligation_compare_ready_count") or 0)

    warnings: list[str] = []
    ready = sections_retrieved > 0
    if routing_active and not obligations:
        ready = False
        warnings.append("pre_compare_join: no obligations extracted for hybrid routing")
    if sections_retrieved == 0:
        warnings.append("pre_compare_join: no section retrieval bundles")
    if routing_active and compare_ready == 0 and obligations:
        warnings.append(
            f"pre_compare_join: zero obligations queued for compare ({len(obligations)} extracted)"
        )

    stats["pipeline_join_ready"] = ready
    stats["pipeline_join_sections"] = sections_retrieved
    stats["pipeline_join_obligations"] = len(obligations)
    stats["pipeline_join_compare_ready"] = compare_ready

    updates: dict[str, Any] = {"compliance_stats": stats}
    if warnings:
        updates["warnings"] = warnings
    return updates
