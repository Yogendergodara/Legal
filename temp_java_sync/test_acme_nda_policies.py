#!/usr/bin/env python3
"""E2E regression: Acme NDA + liability/indemnity policies via Dev UI."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

from bootstrap_env import load_env, setup_pythonpath

load_env()
setup_pythonpath()

from e2e_harness import (  # noqa: E402
    contract_fixture_to_text,
    policy_fixture_to_sync,
    review_text,
    sync_policies,
)

ROOT = Path(__file__).resolve().parent
FIXTURE_DIR = ROOT / "fixtures" / "acme_nda"
CONTRACT_FIXTURE = FIXTURE_DIR / "acme_cloudvendor_nda.json"
POLICY_FIXTURES = [
    FIXTURE_DIR / "policies" / "ms_liability.json",
    FIXTURE_DIR / "policies" / "ms_indemnity.json",
]


async def main() -> int:
    if not CONTRACT_FIXTURE.is_file():
        print(f"Missing fixture: {CONTRACT_FIXTURE}", file=sys.stderr)
        return 1

    contract = json.loads(CONTRACT_FIXTURE.read_text(encoding="utf-8"))
    policies = [
        policy_fixture_to_sync(json.loads(path.read_text(encoding="utf-8")))
        for path in POLICY_FIXTURES
        if path.is_file()
    ]
    if len(policies) < 2:
        print("Missing Acme policy fixtures (ms_liability, ms_indemnity)", file=sys.stderr)
        return 1

    tenant = contract.get("tenant_id", "acme-nda-clean")
    contract_text = contract_fixture_to_text(contract)
    contract_title = contract.get("title") or "Mutual NDA — Acme Corp / CloudVendor Inc."

    async with httpx.AsyncClient(timeout=httpx.Timeout(900.0)) as http:
        health = await http.get("http://localhost:8090/api/health")
        print("health:", health.status_code, health.json().get("document_mcp", {}).get("db"))

        print(f"\n=== Sync {len(policies)} Acme policies (tenant={tenant}) ===")
        sync = await sync_policies(http, policies)
        for p in sync.get("policies", []):
            print(f"  - {p.get('title', p.get('policy_ref'))}: tagger={p.get('tagger')}")

        if os.environ.get("LLM_API_KEY") or os.environ.get("MISTRAL_API_KEY"):
            for p in sync.get("policies", []):
                if p.get("tagger") != "llm":
                    print(f"WARNING: expected tagger=llm, got {p.get('tagger')} for {p.get('policy_ref')}")

        print("\n=== Review (DIRECT) ===")
        out = await review_text(
            http,
            contract_text=contract_text,
            contract_title=contract_title,
            contract_type=contract.get("contract_type") or "nda",
            query="Review this mutual NDA against liability and indemnification policies",
            tenant_id=tenant,
            use_platform=False,
        )
        findings = out.get("findings") or []
        violations = [f for f in findings if f.get("status") == "NON_COMPLIANT"]
        print(f"findings: {len(findings)} | non-compliant: {len(violations)}")
        print("assessment_paths:", out.get("assessment_paths"))
        print("summary:", (out.get("summary_markdown") or out.get("output") or "")[:600])

        if len(findings) > 15:
            print(f"FAIL: finding_count {len(findings)} > 15", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
