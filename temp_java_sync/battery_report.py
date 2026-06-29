#!/usr/bin/env python3
"""Full battery report: current vs previous vs P5."""
from __future__ import annotations

import json
from pathlib import Path

from bootstrap_env import load_env, setup_pythonpath

load_env()
setup_pythonpath()

from beta_test.benchmark_score import score_section_expected, specs_from_legacy_expected
from export_assessment import build_assessment
from validate_p5_golden import CISCO_EXPECTED, _findings_by_section

OUT = Path(__file__).resolve().parent / "outputs"
TH = json.loads((Path(__file__).resolve().parent / "golden_thresholds.json").read_text(encoding="utf-8"))

CONTRACTS = [
    ("cisco", "cisco_review_live.json", "cisco_review_live_prev.json", "cisco_review_p5.json"),
    ("atlassian", "atlassian_review_live.json", "atlassian_review_live_prev.json", "atlassian_review_p5.json"),
    ("ula", "ula_review_live.json", "ula_review_live_prev.json", "ula_review_p5.json"),
    ("eula", "eula_review_live.json", "eula_review_live_prev.json", "eula_review_p5.json"),
    ("nda", "nda_review_live.json", "nda_review_live_prev.json", None),
]


def load(p: Path) -> dict | None:
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def metrics(review: dict | None) -> dict | None:
    if not review:
        return None
    d = review.get("engine_diagnosis") or {}
    ipc = d.get("ipc_summary") or {}
    res = d.get("resilience") or {}
    fun = (d.get("obligation_pipeline") or {}).get("funnel") or {}
    sec = d.get("section_pipeline") or {}
    infra = d.get("infrastructure") or {}
    sc = infra.get("section_compare_batches") or {}
    gr = infra.get("grounding") or {}
    st = review.get("compliance_stats") or {}
    a = build_assessment(review, test_type="rpt")
    legal = None
    try:
        by = _findings_by_section(review)
        _, _, legal = score_section_expected(by, specs_from_legacy_expected(CISCO_EXPECTED))
    except Exception:
        pass
    bi = d.get("baseline_interpretation") or {}
    return {
        "time_s": review.get("elapsed_seconds"),
        "violations": a["violation_count"],
        "weighted": a["scores"]["weighted_alignment_score"],
        "legal_10": legal,
        "ipc": ipc.get("obligation_ipc_rate"),
        "sec_ipc_pct": ipc.get("section_ipc_pct"),
        "rl_429": res.get("llm_rate_limit_events"),
        "posture": res.get("llm_review_posture"),
        "extracted": fun.get("extracted"),
        "queued": fun.get("compare_queued"),
        "obl_llm_b": fun.get("llm_batches") or st.get("obligation_compare_llm_batches"),
        "sec_items": sec.get("compare_items") or st.get("compare_items"),
        "sec_llm_b": sc.get("actual") or st.get("llm_batches_actual"),
        "qr_skip": gr.get("quote_repair_quota_skipped") or st.get("quote_repair_quota_skipped", 0),
        "gnd_failopen": gr.get("grounding_fail_open") or st.get("grounding_fail_open"),
        "ipc_status": (bi.get("ipc_interpretation") or {}).get("status"),
        "flags": bi.get("health_flags") or [],
        "pipeline": d.get("pipeline_mode"),
    }


def f(v, nd=1):
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    if isinstance(v, bool):
        return "Y" if v else "N"
    return str(v)


def delta(cur, prv):
    if cur is None or prv is None:
        return ""
    if isinstance(cur, (int, float)) and isinstance(prv, (int, float)):
        d = cur - prv
        return f"{d:+.1f}" if isinstance(cur, float) else f"{d:+d}"
    return ""


def gate(name: str, m: dict) -> str:
    th = TH.get(name) or {}
    if name == "cisco":
        ms = th.get("min_legal_score_10", 6.0)
        return "PASS" if (m.get("legal_10") or 0) >= ms else "FAIL"
    mv = th.get("min_violations", 0)
    return "PASS" if m.get("violations", 0) >= mv else "FAIL"


def main() -> None:
    rows = []
    for name, cur_f, prv_f, p5_f in CONTRACTS:
        cur = metrics(load(OUT / cur_f))
        prv = metrics(load(OUT / prv_f))
        p5 = metrics(load(OUT / p5_f)) if p5_f else None
        rows.append((name, cur, prv, p5))

    print("LIVE BATTERY REPORT (2026-06-29 run vs previous LIVE vs P5 reference)")
    print("Profile: mistral_conservative | concurrency=2")
    print()

    hdr = f"{'Contract':<11} {'':^5} {'Time':>7} {'Viol':>5} {'Wgt':>6} {'Lgl10':>6} {'IPC':>6} {'429':>5} {'Ext':>5} {'Qued':>5} {'OblB':>5} {'SecI':>5} {'SecB':>5} {'Gate':>5}"
    print(hdr)
    print("-" * len(hdr))

    tot_cur = tot_prv = 0.0
    pass_cur = pass_prv = 0
    for name, cur, prv, p5 in rows:
        for label, m in [("NOW", cur), ("PREV", prv), ("P5", p5)]:
            if not m:
                continue
            g = gate(name, m) if label != "P5" else "-"
            if label == "NOW":
                tot_cur += m.get("time_s") or 0
                if g == "PASS":
                    pass_cur += 1
            if label == "PREV" and g != "-":
                tot_prv += m.get("time_s") or 0
                if g == "PASS":
                    pass_prv += 1
            print(
                f"{name if label=='NOW' else '':<11} {label:<5} "
                f"{f(m.get('time_s')):>7} {f(m.get('violations')):>5} {f(m.get('weighted')):>6} "
                f"{f(m.get('legal_10')):>6} {f(m.get('ipc')):>6} {f(m.get('rl_429')):>5} "
                f"{f(m.get('extracted')):>5} {f(m.get('queued')):>5} {f(m.get('obl_llm_b')):>5} "
                f"{f(m.get('sec_items')):>5} {f(m.get('sec_llm_b')):>5} {g:>5}"
            )
        if cur and prv:
            print(
                f"{'':11} {'CHG':<5} "
                f"{delta(cur.get('time_s'), prv.get('time_s')):>7} {delta(cur.get('violations'), prv.get('violations')):>5} "
                f"{delta(cur.get('weighted'), prv.get('weighted')):>6} {delta(cur.get('legal_10'), prv.get('legal_10')):>6} "
                f"{delta(cur.get('ipc'), prv.get('ipc')):>6} {delta(cur.get('rl_429'), prv.get('rl_429')):>5} "
                f"{delta(cur.get('extracted'), prv.get('extracted')):>5} {delta(cur.get('queued'), prv.get('queued')):>5} "
                f"{delta(cur.get('obl_llm_b'), prv.get('obl_llm_b')):>5} {delta(cur.get('sec_items'), prv.get('sec_items')):>5} "
                f"{delta(cur.get('sec_llm_b'), prv.get('sec_llm_b')):>5}"
            )
        if cur and cur.get("flags"):
            print(f"  flags: {', '.join(cur['flags'][:6])}")
        print()

    print(f"TOTAL wall: NOW {tot_cur:.0f}s ({tot_cur/60:.1f}m) | PREV {tot_prv:.0f}s ({tot_prv/60:.1f}m) | delta {tot_cur-tot_prv:+.0f}s")
    print(f"Gates passed: NOW {pass_cur}/5 | PREV {pass_prv}/5 (cisco counted in prev battery only if file present)")

    batt_prev = load(OUT / "live_contract_battery_prev.json")
    if batt_prev:
        print("\nPrevious battery summary JSON:")
        for r in batt_prev:
            print(f"  {r['test']}: {r.get('elapsed_seconds')}s viol={r.get('violation_count')} gate={r.get('gate_pass')}")


if __name__ == "__main__":
    main()
