#!/usr/bin/env python3
"""IPC-2.3 — Verify all 9 Atlassian policy_ref values are indexed on e2e-demo."""

from __future__ import annotations

import sys

from bootstrap_env import load_env

load_env()

from atlassian_ipc2 import ATLASSIAN_POLICY_REFS, missing_atlassian_refs  # noqa: E402


def main() -> int:
    tenant = "e2e-demo"
    missing = missing_atlassian_refs(tenant)
    print(f"tenant={tenant} expected={len(ATLASSIAN_POLICY_REFS)} missing={len(missing)}")
    if missing:
        for ref in missing:
            print(f"  MISSING: {ref}")
        return 1
    print("OK: all Atlassian policy refs indexed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
