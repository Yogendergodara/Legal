#!/usr/bin/env python3
"""E-IDX — Atlassian policy re-sync + IPC-2 validation (OB-02B index refresh)."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from bootstrap_env import load_env, setup_pythonpath

load_env()
setup_pythonpath()

from atlassian_ipc2 import (  # noqa: E402
    ATLASSIAN_FIXTURE,
    missing_atlassian_refs,
    validate_policy_sync,
)
from atlassian_test_tenant import resolve_atlassian_test_tenant, sync_output_path  # noqa: E402
from e2e_harness import policy_fixture_to_sync  # noqa: E402
from review_agent.clients.document_client import DocumentMCPClient  # noqa: E402
from sync_service import OUTPUTS, sync_policies_only  # noqa: E402

ROOT = Path(__file__).resolve().parent


async def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="E-IDX Atlassian policy sync")
    parser.add_argument("--tenant", default=None, help="Isolated tenant (default: atlassian-test-run)")
    args = parser.parse_args()

    if not ATLASSIAN_FIXTURE.is_file():
        print(f"Missing fixture: {ATLASSIAN_FIXTURE}", file=sys.stderr)
        return 1

    data = json.loads(ATLASSIAN_FIXTURE.read_text(encoding="utf-8"))
    tenant = resolve_atlassian_test_tenant(cli_tenant=args.tenant)
    base_url = os.environ.get("DOCUMENT_SERVER_URL", "http://localhost:8003")
    client = DocumentMCPClient(base_url)

    print(f"=== E-IDX sync (tenant={tenant}, mcp={base_url}) ===")
    sync_result = await sync_policies_only(
        client,
        tenant_id=tenant,
        policies=[policy_fixture_to_sync(p) for p in data["policies"]],
        replace_policies=True,
    )

    sync_path = OUTPUTS / sync_output_path(tenant)
    sync_path.parent.mkdir(exist_ok=True)
    sync_path.write_text(json.dumps(sync_result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {sync_path}")

    errors = validate_policy_sync(sync_result)
    missing = missing_atlassian_refs(tenant)
    preflight = sync_result.get("preflight") or {}
    weak = int(preflight.get("weak_tag_count") or 0)

    print(f"\n=== IPC-2 validation ===")
    print(f"  policies_synced={len(sync_result.get('policies') or [])}")
    print(f"  weak_tag_count={weak}")
    if missing:
        print(f"  missing_refs={missing}")

    if errors:
        print("\nE-IDX FAIL:")
        for err in errors:
            print(f"  - {err}")
        return 2

    if missing:
        print("\nE-IDX FAIL: indexed policy refs incomplete")
        return 3

    if weak:
        print("\nE-IDX FAIL: weak_tag_count > 0 — fix OB-02B tagger on MCP, restart, re-run")
        return 4

    print("\nE-IDX PASS — run smoke: python run_pr01_atlassian_smoke.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
