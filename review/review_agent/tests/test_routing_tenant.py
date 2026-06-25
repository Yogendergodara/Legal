"""Tests for per-tenant routing rollout guard (Phase R9)."""

from __future__ import annotations

from review_agent.config import ReviewSettings
from review_agent.services.routing_tenant import obligation_routing_active


def test_routing_inactive_when_master_off():
    settings = ReviewSettings(obligation_routing_enabled=False)
    assert obligation_routing_active("e2e-demo", settings) is False


def test_routing_active_without_allowlist():
    settings = ReviewSettings(obligation_routing_enabled=True, obligation_routing_tenant_allowlist="")
    assert obligation_routing_active("any-tenant", settings) is True


def test_routing_allowlist_pilot():
    settings = ReviewSettings(
        obligation_routing_enabled=True,
        obligation_routing_tenant_allowlist="e2e-demo,other",
    )
    assert obligation_routing_active("e2e-demo", settings) is True
    assert obligation_routing_active("acme", settings) is False


def test_routing_denylist_blocks():
    settings = ReviewSettings(
        obligation_routing_enabled=True,
        obligation_routing_tenant_denylist="blocked",
    )
    assert obligation_routing_active("blocked", settings) is False
    assert obligation_routing_active("ok", settings) is True
