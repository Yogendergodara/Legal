#!/usr/bin/env python3
"""Sync Xecurify policies + review Xecurify User License Agreement."""

from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

import httpx

from bootstrap_env import load_env, setup_pythonpath

load_env()
setup_pythonpath()

from e2e_harness import review_text, sync_policies  # noqa: E402

ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT / "fixtures" / "xecurify_e2e.json"
CONTRACT = ROOT / "fixtures" / "xecurify_ula_contract.txt"
OUT = ROOT / "outputs" / "xecurify_ula_review.json"


async def main() -> int:
    if not FIXTURE.is_file() or not CONTRACT.is_file():
        print("Missing fixture files", file=sys.stderr)
        return 1

    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    policies = data["policies"]
    contract_text = CONTRACT.read_text(encoding="utf-8")
    tenant = data.get("tenant_id", "e2e-demo")

    async with httpx.AsyncClient(timeout=httpx.Timeout(1200.0)) as http:
        health = await http.get("http://localhost:8090/api/health")
        health.raise_for_status()

        print(f"=== Sync {len(policies)} policies (tenant={tenant}) ===")
        await sync_policies(http, policies, tenant_id=tenant)

        print("\n=== Review Xecurify ULA ===")
        out = await review_text(
            http,
            contract_text=contract_text,
            contract_title="User License Agreement - Xecurify / Customer",
            contract_type="saas",
            query="Review this Xecurify user license agreement against our security, privacy, data retention, incident response, and code of conduct policies",
            tenant_id=tenant,
            use_platform=False,
        )

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    findings = out.get("findings") or []
    by_status = Counter(f.get("status") for f in findings)
    violations = [f for f in findings if f.get("status") == "NON_COMPLIANT"]
    artifact = out.get("artifact") or {}
    sections = artifact.get("sections") or []
    stats = artifact.get("compliance_stats") or {}

    print(f"\nfindings: {len(findings)} | {dict(by_status)}")
    print(f"sections parsed: {len(sections)}")
    print(f"obligation_count: {stats.get('obligation_count')}")
    print(f"obligation_compare_count: {stats.get('obligation_compare_count')}")
    print(f"routing_validation_rejected: {stats.get('routing_validation_rejected')}")
    print(f"compare_items: {stats.get('compare_items')}")
    rs = stats.get("routing_summary") or {}
    if rs:
        print(f"routing_summary: ipc_rate={rs.get('ipc_rate')} compare_rate={rs.get('compare_rate')} wrong_blocked={rs.get('wrong_policy_blocked')}")

    print(f"\nWrote {OUT}")
    for p in out.get("assessment_paths") or []:
        print(f"assessment: outputs/{p}")

    print(f"\nNON_COMPLIANT: {len(violations)}")
    for f in violations[:8]:
        print(f"  [{f.get('contract_section_id')}] {f.get('policy_title')} | {(f.get('rationale') or '')[:100]}")

    print(f"\nWrote {OUT}")
    print((out.get("summary_markdown") or "")[:1500])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
