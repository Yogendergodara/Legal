#!/usr/bin/env python3
"""PR-01 smoke — Atlassian contract via direct MCP (no Dev UI), same path as live battery."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from bootstrap_env import apply_golden_review_defaults, load_env, setup_pythonpath

load_env()
os.environ.setdefault("GOLDEN_LLM_PROFILE_FORCE", "true")
apply_golden_review_defaults()
setup_pythonpath()

from _verify_pr01_settings import PR01_KEYS, resolved_pr01_settings  # noqa: E402
from atlassian_ipc2 import validate_policy_sync  # noqa: E402
from e2e_harness import policy_fixture_to_sync  # noqa: E402
from review_agent.clients.document_client import DocumentMCPClient  # noqa: E402
from review_scope import policy_document_ids_from_sync  # noqa: E402
from run_live_contract_battery import _run_direct_review  # noqa: E402
from sync_service import sync_policies_only  # noqa: E402

ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT / "fixtures" / "atlassian_e2e.json"
CONTRACT = ROOT / "fixtures" / "atlassian_customer_agreement.txt"
OUT = ROOT / "outputs" / "atlassian_pr01_smoke.json"


def _print_resolved_env() -> None:
    out = resolved_pr01_settings()
    print("=== Resolved PR-01 settings (contract bootstrap path) ===")
    for key in PR01_KEYS:
        print(f"  {key}={out.get(key)}")
    print(f"  catalog_match_top_k={out.get('catalog_match_top_k')}")
    print(f"  evidence_expand_max_rounds={out.get('evidence_expand_max_rounds')}")
    print(f"  evidence_rerank_bypass_enabled={out.get('evidence_rerank_bypass_enabled')}")


async def main() -> int:
    _print_resolved_env()

    if not FIXTURE.is_file() or not CONTRACT.is_file():
        print("Missing Atlassian fixtures", file=sys.stderr)
        return 1

    keys = os.environ.get("LLM_API_KEYS", "")
    if "PASTE_KEY" in keys:
        print("WARN: LLM_API_KEYS still has placeholders — 429 likely", file=sys.stderr)

    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    tenant = data.get("tenant_id", "e2e-demo")
    base_url = os.environ.get("DOCUMENT_SERVER_URL", "http://localhost:8003")
    client = DocumentMCPClient(base_url)

    t0 = time.time()
    print(f"\n=== Sync policies (tenant={tenant}) ===")
    sync_result = await sync_policies_only(
        client,
        tenant_id=tenant,
        policies=[policy_fixture_to_sync(p) for p in data["policies"]],
        replace_policies=True,
    )
    sync_errors = validate_policy_sync(sync_result)
    if sync_errors:
        for err in sync_errors:
            print(f"  sync warn: {err}", file=sys.stderr)

    policy_ids = policy_document_ids_from_sync(sync_result)
    print(f"=== Review ({len(CONTRACT.read_text(encoding='utf-8')):,} chars, {len(policy_ids)} policies) ===")
    review = await _run_direct_review(
        client,
        tenant=tenant,
        contract_text=CONTRACT.read_text(encoding="utf-8"),
        contract_title="Atlassian Customer Agreement",
        contract_type="saas",
        query=(
            "Review this Atlassian Customer Agreement against all indexed Atlassian "
            "policies including privacy, acceptable use, DPA, AI terms, and product terms"
        ),
        policy_document_ids=policy_ids,
    )

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(review, indent=2, ensure_ascii=False), encoding="utf-8")

    diagnosis = review.get("engine_diagnosis") or {}
    ipc = diagnosis.get("ipc_summary") or {}
    funnel = (diagnosis.get("obligation_pipeline") or {}).get("funnel") or {}
    violations = sum(1 for f in review.get("findings") or [] if f.get("status") == "NON_COMPLIANT")
    elapsed = round(time.time() - t0, 1)

    print(f"\n=== PR-01 smoke results ({elapsed}s) ===")
    print(f"  NC violations: {violations}")
    print(f"  obligation_ipc_rate: {ipc.get('obligation_ipc_rate')}")
    print(f"  section_ipc_pct: {ipc.get('section_ipc_pct')}")
    print(f"  compare_queued: {funnel.get('compare_queued')}")
    print(f"  post_validation_compared: {(diagnosis.get('obligation_pipeline') or {}).get('routing_summary')}")
    stats = (review.get("artifact") or {}).get("compliance_stats") or {}
    print(f"  obligation_compare_count: {stats.get('obligation_compare_count')}")
    print(f"  llm_rate_limit_events: {(diagnosis.get('resilience') or {}).get('llm_rate_limit_events')}")
    skip = ipc.get("skip_by_reason") or {}
    if skip:
        print(f"  ipc_skip_by_reason: {skip}")
    print(f"\nWrote {OUT}")

    # PR-01 leading indicators (429 may still block NC)
    issues: list[str] = []
    if ipc.get("obligation_ipc_rate", 1) > 0.5:
        issues.append(f"obligation_ipc_rate={ipc.get('obligation_ipc_rate')} > 0.5")
    rq = funnel.get("compare_queued") or 0
    if rq < 20:
        issues.append(f"compare_queued={rq} < 20")
    rs = skip.get("routing_or_skip") or 0
    if rs > 15:
        issues.append(f"routing_or_skip={rs} > 15")
    lco = skip.get("low_concept_overlap") or 0
    if lco > 8:
        issues.append(f"low_concept_overlap={lco} > 8")
    rate_events = (diagnosis.get("resilience") or {}).get("llm_rate_limit_events") or 0
    if rate_events > 5:
        issues.append(f"llm_rate_limit_events={rate_events} (429 — fix keys/quota)")

    if issues:
        print("\nPR-01 gates not met (leading indicators):", file=sys.stderr)
        for item in issues:
            print(f"  - {item}", file=sys.stderr)
        return 2
    print("\nPR-01 leading indicators PASSED", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
