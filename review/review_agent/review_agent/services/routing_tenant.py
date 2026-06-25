"""Per-tenant obligation routing rollout guard (Phase R9)."""

from __future__ import annotations

from review_agent.config import ReviewSettings


def _parse_tenant_list(raw: str) -> set[str]:
    return {part.strip() for part in (raw or "").split(",") if part.strip()}


def obligation_routing_active(tenant_id: str, settings: ReviewSettings) -> bool:
    if not settings.obligation_routing_enabled:
        return False
    allow = _parse_tenant_list(settings.obligation_routing_tenant_allowlist)
    if allow and tenant_id not in allow:
        return False
    deny = _parse_tenant_list(settings.obligation_routing_tenant_denylist)
    if deny and tenant_id in deny:
        return False
    return True
