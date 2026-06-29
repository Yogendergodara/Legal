#!/usr/bin/env python3
"""Compare section-first vs obligation-on vs previous hybrid battery."""
from __future__ import annotations

import json
from pathlib import Path

from bootstrap_env import apply_golden_review_defaults, load_env, setup_pythonpath

load_env()
apply_golden_review_defaults()
setup_pythonpath()

from export_assessment import build_assessment

OUT = Path(__file__).resolve().parent / "outputs"


def metrics(path: Path) -> dict | None:
    if not path.is_file():
        return None
    r = json.loads(path.read_text(encoding="utf-8"))
    d = r.get("engine_diagnosis") or {}
    ipc = d.get("ipc_summary") or {}
    res = d.get("resilience") or {}
    obl = d.get("obligation_pipeline") or {}
    funnel = obl.get("funnel") or {}
    sec = d.get("section_pipeline") or {}
    a = build_assessment(r, test_type="x")
    infra = d.get("infrastructure") or {}
    adv = d.get("config_advisory") or {}
    return {
        "elapsed": r.get("elapsed_seconds"),
        "pipeline": d.get("pipeline_mode"),
        "routing_active": adv.get("obligation_routing_active"),
        "violations": a["violation_count"],
        "weighted": a["scores"]["weighted_alignment_score"],
        "section_ipc_pct": ipc.get("section_ipc_pct"),
        "obl_ipc_rate": ipc.get("obligation_ipc_rate")
        or (obl.get("routing_summary") or {}).get("ipc_rate"),
        "rate_limits": res.get("llm_rate_limit_events"),
        "extracted": funnel.get("extracted"),
        "compare_queued": funnel.get("compare_queued"),
        "obl_batches": funnel.get("llm_batches"),
        "sec_compare_items": sec.get("compare_items"),
        "sec_batches": (infra.get("section_compare_batches") or {}).get("actual"),
        "findings": r.get("finding_count"),
    }


def main() -> None:
    contracts = ["atlassian", "ula", "eula", "nda"]
    prev_b = {x["test"]: x for x in json.loads((OUT / "live_contract_battery_prev.json").read_text())}
    sec_b = {
        x["test"]: x
        for x in json.loads((OUT / "live_contract_battery_section_first_allowlist_bug.json").read_text())
    }
    obl_b = {x["test"]: x for x in json.loads((OUT / "live_contract_battery.json").read_text())}

    print("=" * 110)
    print("THREE-WAY: PREVIOUS hybrid | SECTION-FIRST (routing off) | OBLIGATION ON (hybrid, fixed allowlist)")
    print("=" * 110)
    hdr = (
        f"{'Contract':<10} {'Prev NC':>8} {'Sec NC':>8} {'Obl NC':>8} "
        f"{'Prev t':>8} {'Sec t':>8} {'Obl t':>8} "
        f"{'Obl extr':>9} {'Obl cmpQ':>9} {'429':>5} {'SecIPC%':>8} {'OblIPC':>7}"
    )
    print(hdr)
    print("-" * 110)
    for c in contracts:
        mp = metrics(OUT / f"{c}_review_live_prev.json") or {}
        ms = metrics(OUT / f"{c}_review_live_section_first.json") or metrics(OUT / f"{c}_review_live.json")
        # section_first backup may not exist for all; use sec_b summary
        mo = metrics(OUT / f"{c}_review_live.json") or {}
        p, s, o = prev_b.get(c, {}), sec_b.get(c, {}), obl_b.get(c, {})
        print(
            f"{c:<10} {p.get('violation_count', '?'):>8} "
            f"{s.get('violation_count', '?'):>8} {o.get('violation_count', '?'):>8} "
            f"{p.get('elapsed_seconds', '?'):>8} {s.get('elapsed_seconds', '?'):>8} {o.get('elapsed_seconds', '?'):>8} "
            f"{str(mo.get('extracted', '—')):>9} {str(mo.get('compare_queued', '—')):>9} "
            f"{str(mo.get('rate_limits', '—')):>5} {str(mo.get('section_ipc_pct', '—')):>8} {str(mo.get('obl_ipc_rate', '—')):>7}"
        )
        print(f"  pipeline: prev={mp.get('pipeline')} sec={s.get('pipeline_mode')} obl={mo.get('pipeline')} routing_active={mo.get('routing_active')}")


if __name__ == "__main__":
    main()
