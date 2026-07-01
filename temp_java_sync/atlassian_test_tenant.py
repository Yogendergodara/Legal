"""Isolated tenant for Atlassian contract smoke (no policy conflict with atlassian-demo / e2e-demo)."""

from __future__ import annotations

import os
import re

DEFAULT_ATLASSIAN_TEST_TENANT_ID = "atlassian-test-run"


def normalize_tenant_id(raw: str) -> str:
    """Lowercase; spaces/underscores → hyphen; strip invalid chars."""
    value = (raw or "").strip().lower()
    value = re.sub(r"[\s_]+", "-", value)
    value = re.sub(r"[^a-z0-9-]", "", value)
    value = re.sub(r"-+", "-", value).strip("-")
    if value.startswith("attlassian"):
        value = "atlassian" + value[len("attlassian") :]
    return value or DEFAULT_ATLASSIAN_TEST_TENANT_ID


def resolve_atlassian_test_tenant(*, cli_tenant: str | None = None) -> str:
    """Priority: CLI > ATLASSIAN_TEST_TENANT_ID env > default atlassian-test-run."""
    if cli_tenant and cli_tenant.strip():
        return normalize_tenant_id(cli_tenant)
    env_val = os.environ.get("ATLASSIAN_TEST_TENANT_ID", "").strip()
    if env_val:
        return normalize_tenant_id(env_val)
    return DEFAULT_ATLASSIAN_TEST_TENANT_ID


def sync_output_path(tenant_id: str) -> str:
    safe = normalize_tenant_id(tenant_id)
    return f"sync_{safe}.json"


def review_output_path(tenant_id: str) -> str:
    safe = normalize_tenant_id(tenant_id)
    return f"atlassian_{safe}_smoke.json"
