#!/usr/bin/env python3
"""E2E test: Xecurify policies + NDA contract (direct; optional platform)."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx

from bootstrap_env import load_env, setup_pythonpath

load_env()
setup_pythonpath()

from e2e_harness import review_text, sync_policies  # noqa: E402

ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT / "fixtures" / "xecurify_e2e.json"


async def main() -> int:
    parser = argparse.ArgumentParser(description="Xecurify Dev UI E2E smoke")
    parser.add_argument(
        "--platform",
        action="store_true",
        help="Also run review via legal_ai_platform :8080",
    )
    args = parser.parse_args()

    if not FIXTURE.is_file():
        print(f"Missing fixture: {FIXTURE}", file=sys.stderr)
        return 1

    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    policies = data["policies"]
    contract_text = data["contract_text"]
    tenant = data.get("tenant_id", "e2e-demo")

    async with httpx.AsyncClient(timeout=httpx.Timeout(900.0)) as http:
        health = await http.get("http://localhost:8090/api/health")
        print("health:", health.status_code, health.json().get("document_mcp", {}).get("db"))

        print(f"\n=== Sync {len(policies)} policies (tenant={tenant}) ===")
        sync = await sync_policies(http, policies)
        for p in sync.get("policies", []):
            print(f"  - {p.get('title', p.get('policy_ref'))}: {p.get('categories', [])} tagger={p.get('tagger')}")

        review_body = {
            "contract_text": contract_text,
            "contract_title": "Mutual NDA - Xecurify / Recipient",
            "contract_type": "nda",
            "query": (
                "Review this mutual NDA against our Code of Conduct, data retention, "
                "security, and privacy policies"
            ),
            "tenant_id": tenant,
        }

        modes = [("DIRECT", False)]
        if args.platform:
            modes.append(("PLATFORM", True))

        for label, use_platform in modes:
            print(f"\n=== Review ({label}) ===")
            try:
                out = await review_text(http, use_platform=use_platform, **review_body)
            except httpx.HTTPStatusError as exc:
                detail = exc.response.text[:1500] if exc.response is not None else str(exc)
                print("status:", exc.response.status_code if exc.response else "?", "error:", detail)
                if use_platform:
                    continue
                return 1
            findings = out.get("findings") or []
            violations = [f for f in findings if f.get("status") == "NON_COMPLIANT"]
            print(f"findings: {len(findings)} | non-compliant: {len(violations)}")
            print("assessment_paths:", out.get("assessment_paths"))
            print("summary:", (out.get("summary_markdown") or out.get("output") or "")[:800])
            for f in violations[:5]:
                print(
                    f"  [{f.get('contract_section_id')}] {f.get('dimension_label')}: "
                    f"{(f.get('rationale') or '')[:120]}"
                )

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
