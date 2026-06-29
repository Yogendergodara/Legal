"""Tests for golden tenant rollout defaults."""

from __future__ import annotations

import os

from bootstrap_env import apply_golden_tenant_rollout_defaults


def test_apply_golden_tenant_rollout_defaults_global(monkeypatch):
    monkeypatch.delenv("OBLIGATION_ROUTING_ENABLED", raising=False)
    monkeypatch.delenv("REVIEW_PIPELINE_MODE", raising=False)
    monkeypatch.delenv("OBLIGATION_ROUTING_TENANT_ALLOWLIST", raising=False)
    monkeypatch.delenv("OBLIGATION_ROUTING_TENANT_DENYLIST", raising=False)
    monkeypatch.delenv("REVIEW_PIPELINE_TENANT_ALLOWLIST", raising=False)
    apply_golden_tenant_rollout_defaults()
    assert os.environ["OBLIGATION_ROUTING_ENABLED"] == "true"
    assert os.environ["REVIEW_PIPELINE_MODE"] == "parallel_hybrid"
    assert not os.environ.get("OBLIGATION_ROUTING_TENANT_ALLOWLIST", "").strip()
    assert not os.environ.get("REVIEW_PIPELINE_TENANT_ALLOWLIST", "").strip()
