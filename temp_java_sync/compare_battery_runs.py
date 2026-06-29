#!/usr/bin/env python3
"""Compare LIVE battery runs: time, accuracy, IPC, LLM calls."""
from __future__ import annotations

import json
from pathlib import Path

from bootstrap_env import load_env, setup_pythonpath

load_env()
setup_pythonpath()

from beta_test.benchmark_score import score_section_expected, specs_from_legacy_expected
from export_assessment import build_assessment
from validate_p5_golden import CISCO_EXPECTED, _findings_by_section

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs"
TH = json.loads((ROOT / "golden_thresholds.json").read_text(encoding="utf-8"))

CONTRACTS = [
    ("cisco", "cisco_review_live.json", "cisco_review_p5.json"),
    ("atlassian", "atlassian_review_live.json", "atlassian_review_p5.json"),
    ("ula", "ula_review_live.json", "ula_review_p5.json"),
    ("eula", "eula_review_live.json", "eula_review_p5.json"),
    ("nda", "nda_review_live.json", None),
]


def _load(path: Path) -> dict | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _metrics(review: dict | None) -> dict | None:
    if not review:
        return None
    diag = review.get("engine_diagnosis") or {}
    ipc = diag.get("ipc_summary") or {}
    res = diag.get("resilience") or {}
    obl = diag.get("obligation_pipeline") or {}
    funnel = obl.get("funnel") or {}
    sec = diag.get("section_pipeline") or {}
    infra = (diag.get("infrastructure") or {})
    grounding = infra.get("grounding") or {}
    assess = build_assessment(review, test_type="compare")
    stats = review.get("compliance_stats") or {}

    legal_score = None
    if review.get("artifacts"):
        try:
            by = _findings_by_section(review)
            specs = specs_from_legacy_expected(CISCO_EXPECTED)
            _, _, legal_score = score_section_expected(by, specs)
        except Exception:
            pass

    return {
        "elapsed_s": review.get("elapsed_seconds"),
        "violations": assess["violation_count"],
        "weighted": assess["scores"]["weighted_alignment_score"],
        "legal_score_10": legal_score,
        "ipc_rate": ipc.get("obligation_ipc_rate"),
        "section_ipc_pct": ipc.get("section_ipc_pct"),
        "rate_limit_events": res.get("llm_rate_limit_events"),
        "posture": res.get("llm_review_posture"),
        "extracted": funnel.get("extracted"),
        "compare_queued": funnel.get("compare_queued"),
        "obl_batches": funnel.get("llm_batches") or stats.get("obligation_compare_llm_batches"),
        "section_compare_items": sec.get("compare_items") or stats.get("compare_items"),
        "section_batches": (infra.get("section_compare_batches") or {}).get("actual") or stats.get("llm_batches_actual"),
        "quote_repair_skipped": grounding.get("quote_repair_quota_skipped") or stats.get("quote_repair_quota_skipped", 0),
        "grounding_fail_open": grounding.get("grounding_fail_open") or stats.get("grounding_fail_open"),
        "ipc_interp": (diag.get("baseline_interpretation") or {}).get("ipc_interpretation", {}).get("status"),
        "health_flags": (diag.get("baseline_interpretation") or {}).get("health_flags") or [],
    }


def _fmt(v, nd=1):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def main() -> None:
    prev_battery = _load(OUT / "live_contract_battery_prev.json")
    curr_battery = _load(OUT / "live_contract_battery.json")

    print("=" * 100)
    print("LIVE CONTRACT BATTERY — METRICS COMPARISON (current vs previous LIVE vs P5 reference)")
    print("=" * 100)

    for name, live_file, p5_file in CONTRACTS:
        th = TH.get(name) or {}
        live = _metrics(_load(OUT / live_file))
        prev = _metrics(_load(OUT / f"{live_file.replace('.json', '_prev.json')}"))
        p5 = _metrics(_load(OUT / p5_file)) if p5_file else None

        print(f"\n### {name.upper()} (min_violations={th.get('min_violations', '?')})")
        print(f"{'Metric':<28} {'CURRENT':<14} {'PREVIOUS':<14} {'P5 REF':<14} {'chg vs prev':<12}")
        print("-" * 82)
        if not live:
            print("  (no current run)")
            continue
        keys = [
            ("elapsed_s", "Wall time (s)"),
            ("violations", "NC violations"),
            ("weighted", "Weighted score"),
            ("legal_score_10", "Legal score /10"),
            ("ipc_rate", "Obligation IPC rate"),
            ("section_ipc_pct", "Section IPC %"),
            ("rate_limit_events", "429 / rate-limit"),
            ("extracted", "Obligations extracted"),
            ("compare_queued", "Compare queued"),
            ("obl_batches", "Obligation LLM batches"),
            ("section_compare_items", "Section compare items"),
            ("section_batches", "Section LLM batches"),
            ("quote_repair_skipped", "Quote repair skipped"),
            ("grounding_fail_open", "Grounding fail-open"),
            ("ipc_interp", "IPC interpretation"),
        ]
        for key, label in keys:
            cur = live.get(key)
            prv = prev.get(key) if prev else None
            ref = p5.get(key) if p5 else None
            delta = ""
            if prv is not None and cur is not None and isinstance(cur, (int, float)) and isinstance(prv, (int, float)):
                d = cur - prv
                delta = f"{d:+.1f}" if isinstance(cur, float) else f"{d:+d}"
            print(f"{label:<28} {_fmt(cur):<14} {_fmt(prv):<14} {_fmt(ref):<14} {delta:<12}")

        min_v = th.get("min_violations", 0)
        gate = "PASS" if live["violations"] >= min_v else "FAIL"
        if name == "cisco":
            min_s = th.get("min_legal_score_10", 6.0)
            gate = "PASS" if (live.get("legal_score_10") or 0) >= min_s else "FAIL"
        print(f"  Gate: {gate}")
        if live.get("health_flags"):
            print(f"  Health flags: {', '.join(live['health_flags'][:8])}")

    print("\n" + "=" * 100)
    print("BATTERY SUMMARY")
    print("=" * 100)
    if curr_battery:
        total_time = sum(r.get("elapsed_seconds") or 0 for r in curr_battery)
        passed = sum(1 for r in curr_battery if r.get("gate_pass"))
        print(f"Current: {passed}/{len(curr_battery)} gates passed, total wall {total_time:.0f}s ({total_time/60:.1f} min)")
    if prev_battery:
        total_time = sum(r.get("elapsed_seconds") or 0 for r in prev_battery)
        passed = sum(1 for r in prev_battery if r.get("gate_pass"))
        print(f"Previous: {passed}/{len(prev_battery)} gates passed, total wall {total_time:.0f}s ({total_time/60:.1f} min)")


if __name__ == "__main__":
    main()
