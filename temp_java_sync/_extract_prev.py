import json
from pathlib import Path

OUT = Path("outputs")
files = [
    ("cisco", "cisco_review_live_prev.json", "cisco_review_p5.json"),
    ("atlassian", "atlassian_review_live_prev.json", "atlassian_review_p5.json"),
    ("ula", "ula_review_live_prev.json", "ula_review_p5.json"),
    ("eula", "eula_review_live_prev.json", "eula_review_p5.json"),
    ("nda", "nda_review_live_prev.json", None),
]
for name, prev, p5 in files:
    for label, f in [("PREV", prev), ("P5", p5)]:
        if not f or not (OUT / f).exists():
            continue
        r = json.loads((OUT / f).read_text(encoding="utf-8"))
        d = r.get("engine_diagnosis") or {}
        ipc = d.get("ipc_summary") or {}
        res = d.get("resilience") or {}
        fun = (d.get("obligation_pipeline") or {}).get("funnel") or {}
        sec = d.get("section_pipeline") or {}
        infra = d.get("infrastructure") or {}
        sc = infra.get("section_compare_batches") or {}
        v = sum(1 for x in (r.get("findings") or []) if x.get("status") == "NON_COMPLIANT")
        print(
            f"{name:10} {label:4} time={r.get('elapsed_seconds')}s viol={v} "
            f"ipc={ipc.get('obligation_ipc_rate')} 429={res.get('llm_rate_limit_events')} "
            f"ext={fun.get('extracted')} queued={fun.get('compare_queued')} "
            f"obl_b={fun.get('llm_batches')} sec_items={sec.get('compare_items')} sec_b={sc.get('actual')}"
        )
