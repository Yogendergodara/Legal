#!/usr/bin/env python3
"""Sync Atlassian policies + review Atlassian Customer Agreement (vendor-matched test)."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections import Counter
from pathlib import Path

import httpx

from bootstrap_env import apply_golden_review_defaults, load_env, setup_pythonpath

load_env()
os.environ.setdefault("GOLDEN_LLM_PROFILE_FORCE", "true")
apply_golden_review_defaults()
setup_pythonpath()

from atlassian_ipc2 import SYNC_OUT, validate_policy_sync  # noqa: E402
from e2e_harness import review_text, sync_policies  # noqa: E402
from review_scope import policy_document_ids_from_sync  # noqa: E402

try:
    from review_agent.services.baseline_interpretation import (  # noqa: E402
        build_baseline_interpretation,
        has_accuracy_regression,
        load_baseline_profile,
    )
except ImportError:
    build_baseline_interpretation = None  # type: ignore[misc, assignment]
    has_accuracy_regression = None  # type: ignore[misc, assignment]
    load_baseline_profile = None  # type: ignore[misc, assignment]

ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT / "fixtures" / "atlassian_e2e.json"
CONTRACT = ROOT / "fixtures" / "atlassian_customer_agreement.txt"
OUT = ROOT / "outputs" / "atlassian_review.json"
ASSESSMENT = ROOT / "outputs" / "atlassian_review_assessment.json"
BASELINE_PROFILE = "atlassian_v1"


def _resolve_baseline_interpretation(
    diagnosis: dict,
    stats: dict,
    review: dict,
) -> dict | None:
    interp = diagnosis.get("baseline_interpretation")
    if interp:
        return interp
    if not build_baseline_interpretation or not load_baseline_profile:
        return None
    baseline_path = ROOT / "baselines" / f"{BASELINE_PROFILE}.json"
    baseline = load_baseline_profile(BASELINE_PROFILE)
    if baseline is None and baseline_path.is_file():
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    if not baseline:
        return None
    enriched_stats = dict(stats)
    if not enriched_stats.get("non_compliant_count"):
        enriched_stats["non_compliant_count"] = sum(
            1 for f in review.get("findings") or [] if f.get("status") == "NON_COMPLIANT"
        )
    return build_baseline_interpretation(diagnosis, enriched_stats, baseline=baseline)


async def main() -> int:
    fetch_script = ROOT / "fetch_atlassian_full_fixtures.py"
    if "--fetch" in sys.argv and fetch_script.is_file():
        import subprocess

        print("=== Fetching full Atlassian legal documents ===")
        subprocess.run([sys.executable, str(fetch_script)], check=True)
    elif not FIXTURE.is_file() or not CONTRACT.is_file():
        print("Missing fixtures; run with --fetch first", file=sys.stderr)
        return 1

    if not FIXTURE.is_file() or not CONTRACT.is_file():
        print("Missing Atlassian fixture files", file=sys.stderr)
        return 1

    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    policies = data["policies"]
    contract_text = CONTRACT.read_text(encoding="utf-8")
    tenant = data.get("tenant_id", "e2e-demo")
    print(
        f"contract: {len(contract_text):,} chars | "
        f"policies: {len(policies)} ({sum(len(p.get('text','')) for p in policies):,} chars)"
    )

    async with httpx.AsyncClient(timeout=httpx.Timeout(7200.0)) as http:
        health = await http.get("http://localhost:8090/api/health")
        health.raise_for_status()

        print(f"=== Sync {len(policies)} Atlassian policies (tenant={tenant}, replace=True) ===")
        sync = await sync_policies(http, policies, tenant_id=tenant, replace=True)
        sync_errors = validate_policy_sync(sync)
        SYNC_OUT.parent.mkdir(exist_ok=True)
        SYNC_OUT.write_text(json.dumps(sync, indent=2, ensure_ascii=False), encoding="utf-8")
        for policy in sync.get("policies") or []:
            print(f"  - {policy.get('policy_ref')}: tagger={policy.get('tagger')}")
        if sync_errors:
            print("Sync validation warnings:", file=sys.stderr)
            for err in sync_errors:
                print(f"  {err}", file=sys.stderr)

        if "--sync-only" in sys.argv:
            return 1 if sync_errors else 0

        print("\n=== Review Atlassian Customer Agreement ===")
        policy_ids = policy_document_ids_from_sync(sync)
        out = await review_text(
            http,
            contract_text=contract_text,
            contract_title="Atlassian Customer Agreement",
            contract_type="saas",
            query=(
                "Review this Atlassian Customer Agreement against all indexed Atlassian "
                "policies including privacy policy, acceptable use policy, data processing "
                "addendum, AI terms, product-specific terms, third-party code policy, "
                "advisory services policy, government amendment, and copyright policy"
            ),
            tenant_id=tenant,
            use_platform=False,
            policy_document_ids=policy_ids,
        )

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    findings = out.get("findings") or []
    by_status = Counter(f.get("status") for f in findings)
    violations = [f for f in findings if f.get("status") == "NON_COMPLIANT"]
    ipc = [f for f in findings if f.get("status") == "INSUFFICIENT_POLICY_COVERAGE"]
    artifact = out.get("artifact") or {}
    sections = artifact.get("sections") or []
    stats = artifact.get("compliance_stats") or {}
    report_meta = (out.get("artifacts") or {}).get("report", {}).get("metadata") or {}
    diagnosis = (
        out.get("engine_diagnosis")
        or report_meta.get("engine_diagnosis")
        or artifact.get("engine_diagnosis")
        or {}
    )
    ipc_summary = diagnosis.get("ipc_summary") or {}

    print(f"\nfindings: {len(findings)} | {dict(by_status)}")
    print(f"sections parsed: {len(sections)}")
    if sections:
        print("section ids:", ", ".join(s.get("section_id", "?") for s in sections[:25]))
        if len(sections) > 25:
            print(f"  ... +{len(sections) - 25} more")
    print(f"obligation_count: {stats.get('obligation_count')}")
    print(f"obligation_compare_count: {stats.get('obligation_compare_count')}")
    print(f"routing_validation_rejected: {stats.get('routing_validation_rejected')}")
    print(f"compare_items: {stats.get('compare_items')}")
    print(f"weighted_alignment_score: {stats.get('weighted_alignment_score')}")
    rs = stats.get("routing_summary") or {}
    if ipc_summary:
        print(
            f"engine_diagnosis: pipeline={diagnosis.get('pipeline_mode')} "
            f"obligation_ipc_rate={ipc_summary.get('obligation_ipc_rate')} "
            f"section_ipc_pct={ipc_summary.get('section_ipc_pct')}"
        )
        skip = ipc_summary.get("skip_by_reason") or {}
        if skip:
            print(f"ipc_skip_by_reason: {dict(skip)}")
    elif rs:
        print(
            f"routing_summary: ipc_rate={rs.get('ipc_rate')} "
            f"compare_rate={rs.get('compare_rate')} "
            f"wrong_blocked={rs.get('wrong_policy_blocked')}"
        )

    baseline_interp = _resolve_baseline_interpretation(diagnosis, stats, out)
    if baseline_interp:
        print(f"\nbaseline funnel: {baseline_interp.get('funnel_story')}")
        deltas = baseline_interp.get("deltas") or {}
        for metric, payload in deltas.items():
            if isinstance(payload, dict):
                print(
                    f"  {metric}: actual={payload.get('actual')} "
                    f"baseline={payload.get('baseline')} status={payload.get('status')}"
                )
        flags = baseline_interp.get("health_flags") or []
        if flags:
            print(f"  health_flags: {flags}")
        accuracy = baseline_interp.get("primary_accuracy") or {}
        print(
            f"  primary_accuracy: nc={accuracy.get('violations_nc')} "
            f"min={accuracy.get('baseline_min')} status={accuracy.get('status')}"
        )
        if has_accuracy_regression and has_accuracy_regression(baseline_interp):
            print("\nACCURACY REGRESSION: violations below baseline minimum", file=sys.stderr)
            return 2

    for p in out.get("assessment_paths") or []:
        ap = ROOT / "outputs" / p
        if ap.is_file():
            ASSESSMENT.write_text(ap.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"assessment: {ASSESSMENT}")

    print(f"\nNON_COMPLIANT ({len(violations)}):")
    for f in violations[:12]:
        print(
            f"  [{f.get('contract_section_id')}] {f.get('policy_title')} | "
            f"{(f.get('rationale') or '')[:120]}"
        )

    print(f"\nINSUFFICIENT_POLICY_COVERAGE ({len(ipc)}):")
    for f in ipc[:8]:
        print(
            f"  [{f.get('contract_section_id')}] {f.get('policy_title') or 'no policy'} | "
            f"{(f.get('rationale') or '')[:100]}"
        )

    print(f"\nWrote {OUT}")
    print((out.get("summary_markdown") or "")[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
