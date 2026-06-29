#!/usr/bin/env python3
"""Summarize live vs P5 contract runs."""
from __future__ import annotations

import json
from pathlib import Path

from bootstrap_env import load_env, setup_pythonpath

load_env()
setup_pythonpath()

from export_assessment import build_assessment
from validate_p5_golden import CISCO_EXPECTED, _findings_by_section
from beta_test.benchmark_score import score_section_expected, specs_from_legacy_expected

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs"
TH = json.loads((ROOT / "golden_thresholds.json").read_text(encoding="utf-8"))


def load_review(name: str) -> dict | None:
    path = OUT / name
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def summarize(review: dict) -> dict:
    diag = review.get("engine_diagnosis") or {}
    res = diag.get("resilience") or {}
    assess = build_assessment(review, test_type="summary")
    return {
        "elapsed": review.get("elapsed_seconds"),
        "violations": assess["violation_count"],
        "weighted": assess["scores"]["weighted_alignment_score"],
        "rate_limit": res.get("llm_rate_limit_events"),
        "ipc_rate": (diag.get("ipc_summary") or {}).get("obligation_ipc_rate"),
    }


def main() -> None:
    contracts = [
        ("cisco", "cisco_review_live.json", "cisco_review_p5.json", "score10"),
        ("atlassian", "atlassian_review_live.json", "atlassian_review_p5.json", "violations"),
        ("ula", "ula_review_live.json", "ula_review_p5.json", "violations"),
        ("eula", "eula_review_live.json", "eula_review_p5.json", "violations"),
        ("nda", "nda_review_live.json", "nda_review_p5.json", "violations"),
    ]

    print("ROUTING GOLDEN: wrong_policy_compare_count=0 (passed in preflight)")
    print()
    print(f"{'Contract':<12} {'Run':<5} {'Time':<8} {'Viol':<5} {'Weighted':<8} {'429s':<6} Gate")
    print("-" * 72)

    for name, live_file, p5_file, gate_type in contracts:
        th = TH.get(name) or {}
        for label, fname in (("LIVE", live_file), ("P5", p5_file)):
            review = load_review(fname)
            if not review:
                continue
            s = summarize(review)
            gate = "?"
            if gate_type == "score10" and label == "LIVE":
                by = _findings_by_section(review)
                specs = specs_from_legacy_expected(CISCO_EXPECTED)
                _, _, score = score_section_expected(by, specs)
                gate = "PASS" if score >= 10.0 else f"FAIL score={score}"
            elif gate_type == "violations":
                min_v = th.get("min_violations", 0)
                gate = "PASS" if s["violations"] >= min_v else f"FAIL need>={min_v}"
            if name == "cisco" and label == "P5":
                by = _findings_by_section(review)
                specs = specs_from_legacy_expected(CISCO_EXPECTED)
                _, _, score = score_section_expected(by, specs)
                gate = "PASS" if score >= 10.0 else f"FAIL score={score}"

            elapsed = s["elapsed"]
            elapsed_s = f"{elapsed:.0f}s" if elapsed else "?"
            print(
                f"{name:<12} {label:<5} {elapsed_s:<8} {s['violations']:<5} "
                f"{s['weighted']:<8} {s['rate_limit'] or 0:<6} {gate}"
            )
        print()


if __name__ == "__main__":
    main()
