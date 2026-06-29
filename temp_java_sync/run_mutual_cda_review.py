#!/usr/bin/env python3
"""Sync Xecurify policies + review Sub-Zero mutual CDA."""

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
CONTRACT = ROOT / "fixtures" / "mutual_cda_subzero.txt"
OUT = ROOT / "outputs" / "mutual_cda_subzero_review.json"


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

        print(f"=== Sync {len(policies)} Xecurify policies (tenant={tenant}) ===")
        sync = await sync_policies(http, policies, tenant_id=tenant)
        for p in sync.get("policies", []):
            print(f"  - {p.get('title', p.get('policy_ref'))}")

        print("\n=== Review Sub-Zero Mutual CDA ===")
        out = await review_text(
            http,
            contract_text=contract_text,
            contract_title="Mutual CDA - Sub-Zero Group Inc.",
            contract_type="nda",
            query=(
                "Review this mutual confidential disclosure agreement against our "
                "security, privacy, data retention, incident response, and code of conduct policies"
            ),
            tenant_id=tenant,
            use_platform=False,
        )

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    findings = out.get("findings") or []
    by_status = Counter(f.get("status") for f in findings)
    violations = [f for f in findings if f.get("status") == "NON_COMPLIANT"]

    print(f"\nfindings: {len(findings)} | status: {dict(by_status)}")
    stats = out.get("compliance_stats") or out.get("artifact", {}).get("compliance_stats") or {}
    if isinstance(out.get("artifact"), dict):
        artifact_stats = (out["artifact"].get("compliance_stats") or {})
        stats = {**stats, **artifact_stats}
    sections = (out.get("artifact") or {}).get("sections") or []
    print(f"sections parsed: {len(sections)}")
    for s in sections[:15]:
        print(f"  - {s.get('section_id')}: {s.get('title', '')[:50]} ({s.get('char_count')} chars)")
    if len(sections) > 15:
        print(f"  ... +{len(sections) - 15} more")
    print(f"obligation_count: {stats.get('obligation_count')}")
    print(f"obligation_compare_count: {stats.get('obligation_compare_count')}")
    print(f"routing_validation_rejected: {stats.get('routing_validation_rejected')}")
    print(f"Wrote {OUT}")

    print("\n--- NON_COMPLIANT ---")
    for f in violations:
        print(
            f"  [{f.get('contract_section_id')}] {f.get('dimension_label')} | "
            f"{f.get('policy_title') or '—'} | {(f.get('rationale') or '')[:120]}"
        )

    print("\n--- Other findings (sample) ---")
    for f in findings[:12]:
        if f.get("status") == "NON_COMPLIANT":
            continue
        print(
            f"  [{f.get('contract_section_id')}] {f.get('status')} | "
            f"{f.get('dimension_label')} | {(f.get('rationale') or '')[:90]}"
        )

    print("\n--- Summary (first 1200 chars) ---")
    print((out.get("summary_markdown") or "")[:1200])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
