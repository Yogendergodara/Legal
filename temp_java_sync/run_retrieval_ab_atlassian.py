#!/usr/bin/env python3
"""SR-01 Atlassian A/B — compare retrieval IPC vs saved baseline (one contract)."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from bootstrap_env import (
    apply_golden_review_defaults,
    apply_sr01_retrieval_defaults,
    load_env,
    setup_pythonpath,
)

load_env()
setup_pythonpath()

from export_assessment import build_assessment
from review_agent.clients.document_client import DocumentMCPClient
from review_agent.config import get_settings

import run_live_contract_battery as batt

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs"
BASELINE_PATH = OUT / "atlassian_review_live.json"
SR01_PATH = OUT / "atlassian_review_sr01.json"
REPORT_PATH = OUT / "atlassian_sr01_ab_report.json"
SPOT_SECTIONS = ("15", "19", "20.4")


def _ipc_reason_counts(review: dict[str, Any]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for finding in review.get("findings") or []:
        if not isinstance(finding, dict):
            continue
        status = finding.get("status") or finding.get("compliance_status")
        if status != "INSUFFICIENT_POLICY_CONTEXT":
            continue
        gap = str(finding.get("gap_type") or "")
        rationale = str(finding.get("rationale") or "")
        if gap == "coverage_gate_ipc" or "coverage=" in rationale:
            if "no_specific_category_overlap" in rationale:
                counts["no_specific_category_overlap"] += 1
            elif "no_relevant_policy_hits" in rationale or "no_policy" in rationale:
                counts["no_policy"] += 1
            else:
                counts["coverage_gate_other"] += 1
        elif gap == "compare_failed" or "429" in rationale or "rate limit" in rationale.lower():
            counts["compare_failed_429"] += 1
        else:
            counts["ipc_other"] += 1
    return counts


def _metrics(review: dict[str, Any] | None) -> dict[str, Any] | None:
    if not review:
        return None
    diag = review.get("engine_diagnosis") or {}
    stats = review.get("compliance_stats") or {}
    assess = build_assessment(review, test_type="sr01_ab")
    spot: dict[str, str] = {}
    for finding in review.get("findings") or []:
        if not isinstance(finding, dict):
            continue
        sid = str(finding.get("section_id") or "")
        if sid in SPOT_SECTIONS:
            spot[sid] = str(finding.get("status") or finding.get("compliance_status") or "?")
    attempts = []
    for bundle in (review.get("section_retrieval_bundles") or []):
        if isinstance(bundle, dict):
            meta = bundle.get("retrieval_meta") or {}
            if meta.get("attempts"):
                attempts.append(len(meta["attempts"]))
    return {
        "elapsed_s": review.get("elapsed_seconds"),
        "violations": assess["violation_count"],
        "weighted": assess["scores"]["weighted_alignment_score"],
        "coverage_gate_ipc": stats.get("coverage_gate_ipc_count")
        or diag.get("coverage_gate_ipc_count"),
        "section_ipc_pct": (diag.get("ipc_summary") or {}).get("section_ipc_pct"),
        "rate_limits": (diag.get("resilience") or {}).get("llm_rate_limit_events"),
        "pipeline_mode": diag.get("pipeline_mode"),
        "ipc_reasons": dict(_ipc_reason_counts(review)),
        "retrieval_attempts_avg": round(sum(attempts) / len(attempts), 2) if attempts else None,
        "spot_sections": spot,
        "meaning_first": (get_settings().retrieval_meaning_first_enabled if review else None),
    }


def _delta(cur: dict | None, base: dict | None, key: str) -> str:
    if not cur or not base:
        return "—"
    c, b = cur.get(key), base.get(key)
    if isinstance(c, (int, float)) and isinstance(b, (int, float)):
        d = c - b
        return f"{d:+.1f}" if isinstance(c, float) else f"{d:+d}"
    return "—"


async def _run_atlassian(*, sr01: bool) -> dict[str, Any]:
    if sr01:
        apply_sr01_retrieval_defaults()
    else:
        os.environ["SR01_RETRIEVAL_OPT_OUT"] = "true"
        os.environ["RETRIEVAL_MEANING_FIRST_ENABLED"] = "false"
        os.environ["RETRIEVAL_CATEGORY_HARD_FILTER"] = "true"
        os.environ["COMPARE_HIT_ALLOW_PRIMARY_FALLBACK"] = "false"

    get_settings.cache_clear()
    settings = get_settings()
    print(
        f"=== SR01={'on' if sr01 else 'off'} "
        f"meaning_first={settings.retrieval_meaning_first_enabled} "
        f"hard_filter={settings.retrieval_category_hard_filter} "
        f"primary_fallback={settings.compare_hit_allow_primary_fallback} ==="
    )

    fixture = ROOT / "fixtures" / "atlassian_e2e.json"
    contract = ROOT / "fixtures" / "atlassian_customer_agreement.txt"
    data = json.loads(fixture.read_text(encoding="utf-8"))
    tenant = data.get("tenant_id", "atlassian-demo")

    async with DocumentMCPClient.open("http://127.0.0.1:8003") as client:
        t0 = time.time()
        row = await batt._run_named_direct(
            client,
            name="atlassian",
            fixture_path=fixture,
            contract_path=contract,
            contract_title="Atlassian Customer Agreement",
            contract_type="saas",
            query="Review this Atlassian Customer Agreement against all indexed Atlassian policies",
            tenant=tenant,
            min_violations=0,
            validate_sync=False,
        )
        review = json.loads((OUT / "atlassian_review_live.json").read_text(encoding="utf-8"))
        review["elapsed_seconds"] = round(time.time() - t0, 1)
        return review


async def main() -> int:
    OUT.mkdir(exist_ok=True)
    apply_golden_review_defaults()

    mode = os.environ.get("SR01_AB_MODE", "sr01_only").strip().lower()
    baseline = _metrics(_load(BASELINE_PATH)) if BASELINE_PATH.is_file() else None

    if mode in ("full", "both"):
        print("Running baseline (SR-01 off)...")
        base_review = await _run_atlassian(sr01=False)
        BASELINE_PATH.write_text(json.dumps(base_review, indent=2, ensure_ascii=False), encoding="utf-8")
        baseline = _metrics(base_review)
        await batt._cooldown_after_review(base_review, "baseline")

    print("Running SR-01...")
    sr01_review = await _run_atlassian(sr01=True)
    SR01_PATH.write_text(json.dumps(sr01_review, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT / "atlassian_review_live.json").write_text(
        json.dumps(sr01_review, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    current = _metrics(sr01_review)

    report = {
        "baseline": baseline,
        "sr01": current,
        "delta": {
            "violations": _delta(current, baseline, "violations"),
            "coverage_gate_ipc": _delta(current, baseline, "coverage_gate_ipc"),
            "section_ipc_pct": _delta(current, baseline, "section_ipc_pct"),
            "elapsed_s": _delta(current, baseline, "elapsed_s"),
        },
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n=== SR-01 ATLASSIAN A/B ===")
    for key in (
        "violations",
        "coverage_gate_ipc",
        "section_ipc_pct",
        "rate_limits",
        "retrieval_attempts_avg",
        "elapsed_s",
    ):
        print(
            f"  {key:24} baseline={baseline.get(key) if baseline else '—':>8} "
            f"sr01={current.get(key):>8} delta={report['delta'].get(key, '—')}"
        )
    print(f"  spot_sections            {current.get('spot_sections')}")
    print(f"  ipc_reasons (sr01)       {current.get('ipc_reasons')}")
    print(f"Wrote {REPORT_PATH}")
    return 0


def _load(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
