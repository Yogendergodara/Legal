#!/usr/bin/env python3
"""IPC-2.3 — Re-sync 9 Atlassian policies (replace=False) and validate tagger quality."""

from __future__ import annotations

import asyncio
import json
import sys

import httpx

from bootstrap_env import load_env, setup_pythonpath

load_env()
setup_pythonpath()

from atlassian_ipc2 import (  # noqa: E402
    ATLASSIAN_FIXTURE,
    SYNC_OUT,
    missing_atlassian_refs,
    validate_policy_sync,
)
from e2e_harness import sync_policies  # noqa: E402

ROOT = ATLASSIAN_FIXTURE.parent.parent


async def main() -> int:
    if not ATLASSIAN_FIXTURE.is_file():
        print(f"Missing fixture: {ATLASSIAN_FIXTURE}", file=sys.stderr)
        return 1

    data = json.loads(ATLASSIAN_FIXTURE.read_text(encoding="utf-8"))
    policies = data["policies"]
    tenant = data.get("tenant_id", "e2e-demo")

    async with httpx.AsyncClient(timeout=httpx.Timeout(7200.0)) as http:
        health = await http.get("http://localhost:8090/api/health")
        health.raise_for_status()
        print(f"=== Sync {len(policies)} Atlassian policies (tenant={tenant}, replace=False) ===")
        sync = await sync_policies(http, policies, tenant_id=tenant, replace=False)

    SYNC_OUT.parent.mkdir(exist_ok=True)
    SYNC_OUT.write_text(json.dumps(sync, indent=2, ensure_ascii=False), encoding="utf-8")

    errors = validate_policy_sync(sync)
    missing = missing_atlassian_refs(tenant)
    if missing:
        errors.append(f"missing indexed refs: {missing}")

    for policy in sync.get("policies") or []:
        print(f"  - {policy.get('policy_ref')}: tagger={policy.get('tagger')}")

    preflight = sync.get("preflight") or {}
    print(f"preflight: weak_tag_count={preflight.get('weak_tag_count', 0)}")
    print(f"Wrote {SYNC_OUT}")

    if errors:
        for err in errors:
            print(f"FAIL: {err}", file=sys.stderr)
        return 1

    print("OK: Atlassian policy sync passed IPC-2 validation")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
