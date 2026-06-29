"""Review pipeline topology rollout guard (PF-1C)."""

from __future__ import annotations

from review_agent.config import ReviewSettings
from review_agent.services.routing_tenant import _parse_tenant_list, obligation_routing_active


def parallel_pipeline_active(tenant_id: str, settings: ReviewSettings) -> bool:
    if settings.review_pipeline_mode != "parallel_hybrid":
        return False
    allow = _parse_tenant_list(settings.review_pipeline_tenant_allowlist)
    if allow and tenant_id not in allow:
        return False
    return True


def hybrid_obligation_path_active(tenant_id: str, settings: ReviewSettings) -> bool:
    return obligation_routing_active(tenant_id, settings)
