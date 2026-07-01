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
from atlassian_test_tenant import (  # noqa: E402
    resolve_atlassian_test_tenant,
    review_output_path,
    sync_output_path,
)
from e2e_harness import policy_fixture_to_sync  # noqa: E402
from review_agent.clients.document_client import DocumentMCPClient  # noqa: E402
from review_scope import policy_document_ids_from_sync  # noqa: E402
from run_live_contract_battery import _run_direct_review  # noqa: E402
from sync_service import OUTPUTS, sync_policies_only  # noqa: E402

ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT / "fixtures" / "atlassian_e2e.json"
CONTRACT = ROOT / "fixtures" / "atlassian_customer_agreement.txt"
OUT = ROOT / "outputs" / "atlassian_pr01_smoke.json"


def _parse_args():
    import argparse

    parser = argparse.ArgumentParser(description="PR-01 Atlassian contract smoke (isolated tenant)")
    parser.add_argument(
        "--tenant",
        default=None,
        help=f"Tenant ID (default: ATLASSIAN_TEST_TENANT_ID or atlassian-test-run)",
    )
    parser.add_argument(
        "-o",
        "--out",
        type=Path,
        default=None,
        help="Review JSON output path",
    )
    parser.add_argument(
        "--review-only",
        action="store_true",
        help="Skip policy index/sync; use existing sync JSON for this tenant",
    )
    parser.add_argument(
        "--sync-json",
        type=Path,
        default=None,
        help="Sync artifact path (default: outputs/sync_<tenant>.json)",
    )
    return parser.parse_args()


def _print_resolved_env() -> None:
    out = resolved_pr01_settings()
    print("=== Resolved PR-01 settings (contract bootstrap path) ===")
    for key in PR01_KEYS:
        print(f"  {key}={out.get(key)}")
    print(f"  catalog_match_top_k={out.get('catalog_match_top_k')}")
    print(f"  evidence_expand_max_rounds={out.get('evidence_expand_max_rounds')}")
    print(f"  evidence_rerank_bypass_enabled={out.get('evidence_rerank_bypass_enabled')}")


async def main() -> int:
    args = _parse_args()
    tenant = resolve_atlassian_test_tenant(cli_tenant=args.tenant)
    out_path = args.out or (OUTPUTS / review_output_path(tenant))

    _print_resolved_env()
    print(f"  atlassian_test_tenant={tenant}")

    if not FIXTURE.is_file() or not CONTRACT.is_file():
        print("Missing Atlassian fixtures", file=sys.stderr)
        return 1

    keys = os.environ.get("LLM_API_KEYS", "")
    if keys and "PASTE_KEY" in keys:
        print("WARN: LLM_API_KEYS still has placeholders — 429 likely", file=sys.stderr)
    elif not os.environ.get("LLM_API_KEY") and not os.environ.get("MISTRAL_API_KEY"):
        print("WARN: LLM_API_KEY not set — review will fail", file=sys.stderr)

    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    base_url = os.environ.get("DOCUMENT_SERVER_URL", "http://localhost:8003")
    client = DocumentMCPClient(base_url)

    t0 = time.time()
    sync_path = args.sync_json or (OUTPUTS / sync_output_path(tenant))

    if args.review_only:
        if not sync_path.is_file():
            print(f"ERROR: missing sync artifact {sync_path} — run without --review-only first", file=sys.stderr)
            return 1
        sync_result = json.loads(sync_path.read_text(encoding="utf-8"))
        print(f"\n=== Review-only (tenant={tenant}, skip sync) ===")
        print(f"  using sync: {sync_path}")
    else:
        print(f"\n=== Sync policies (tenant={tenant}, replace=true) ===")
        sync_result = await sync_policies_only(
            client,
            tenant_id=tenant,
            policies=[policy_fixture_to_sync(p) for p in data["policies"]],
            replace_policies=True,
        )
        sync_path.parent.mkdir(exist_ok=True)
        sync_path.write_text(json.dumps(sync_result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  sync artifact: {sync_path}")
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

    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(review, indent=2, ensure_ascii=False), encoding="utf-8")

    from review_agent.services.ipc3_gates import check_obligation_funnel_identity  # noqa: E402

    stats = (review.get("artifact") or {}).get("compliance_stats") or review.get("compliance_stats") or {}
    funnel_errors = check_obligation_funnel_identity(stats)
    if funnel_errors:
        print("\nFUNNEL IDENTITY FAIL:", file=sys.stderr)
        for err in funnel_errors:
            print(f"  - {err}", file=sys.stderr)
    else:
        print("\nFUNNEL IDENTITY OK", file=sys.stderr)

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
    print(f"\nWrote {out_path}")

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
