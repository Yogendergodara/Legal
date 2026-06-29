#!/usr/bin/env python3
"""One-off: sync Xecurify policies + review WmD German NDA."""

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
CONTRACT = ROOT / "fixtures" / "wmd_nda_contract.txt"
OUT = ROOT / "outputs" / "wmd_nda_review.json"


async def main() -> int:
    if not FIXTURE.is_file() or not CONTRACT.is_file():
        print("Missing fixture files", file=sys.stderr)
        return 1

    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    policies = data["policies"]
    contract_text = CONTRACT.read_text(encoding="utf-8")
    tenant = data.get("tenant_id", "e2e-demo")

    async with httpx.AsyncClient(timeout=httpx.Timeout(900.0)) as http:
        health = await http.get("http://localhost:8090/api/health")
        health.raise_for_status()
        print("health ok:", health.json().get("document_mcp", {}).get("db"))

        print(f"\n=== Sync {len(policies)} Xecurify policies (tenant={tenant}) ===")
        sync = await sync_policies(http, policies, tenant_id=tenant)
        for p in sync.get("policies", []):
            print(f"  - {p.get('title', p.get('policy_ref'))}")

        print("\n=== Review WmD German NDA ===")
        out = await review_text(
            http,
            contract_text=contract_text,
            contract_title="NDA - WIRmachenDRUCK GmbH / Partner",
            contract_type="nda",
            query=(
                "Review this German mutual NDA against our security, privacy, "
                "data retention, incident response, and code of conduct policies"
            ),
            tenant_id=tenant,
            use_platform=False,
        )

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    findings = out.get("findings") or []
    by_status = Counter(f.get("status") for f in findings)
    violations = [f for f in findings if f.get("status") == "NON_COMPLIANT"]
    ipc = [f for f in findings if f.get("status") == "INSUFFICIENT_POLICY_CONTEXT"]

    print(f"\nfindings: {len(findings)}")
    print("status breakdown:", dict(by_status))
    stats = out.get("compliance_stats") or {}
    if stats.get("routing_summary"):
        print("routing_summary:", json.dumps(stats["routing_summary"], indent=2))
    if stats.get("compliance_mode"):
        print("compliance_mode:", stats.get("compliance_mode"))
    print("weighted_alignment_score:", out.get("weighted_alignment_score"))
    print(f"\nWrote {OUT}")

    print("\n--- NON_COMPLIANT ---")
    for f in violations:
        print(
            f"  [{f.get('contract_section_id')}] {f.get('dimension_label')} | "
            f"{f.get('policy_title') or 'no policy'} | "
            f"{(f.get('rationale') or '')[:140]}"
        )

    print("\n--- INSUFFICIENT_POLICY_CONTEXT (sample) ---")
    for f in ipc[:8]:
        print(
            f"  [{f.get('contract_section_id')}] {f.get('dimension_label')} | "
            f"{(f.get('rationale') or '')[:100]}"
        )

    print("\n--- Summary (first 1000 chars) ---")
    print((out.get("summary_markdown") or "")[:1000])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
