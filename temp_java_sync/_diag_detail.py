import json
from pathlib import Path

for name in ["cisco", "atlassian", "ula", "eula", "nda"]:
    r = json.loads((Path("outputs") / f"{name}_review_live.json").read_text(encoding="utf-8"))
    d = r.get("engine_diagnosis") or {}
    bi = d.get("baseline_interpretation") or {}
    ipc = d.get("ipc_summary") or {}
    fun = (d.get("obligation_pipeline") or {}).get("funnel") or {}
    sec = d.get("section_pipeline") or {}
    res = d.get("resilience") or {}
    gr = (d.get("infrastructure") or {}).get("grounding") or {}
    print(f"--- {name} ---")
    print(
        f"  time={r.get('elapsed_seconds')}s pipeline={d.get('pipeline_mode')} "
        f"429={res.get('llm_rate_limit_events')} posture={res.get('llm_review_posture')}"
    )
    print(f"  ipc_rate={ipc.get('obligation_ipc_rate')} sec_ipc={ipc.get('section_ipc_pct')}")
    print(f"  funnel extracted={fun.get('extracted')} queued={fun.get('compare_queued')} obl_batches={fun.get('llm_batches')}")
    print(f"  section compare_items={sec.get('compare_items')}")
    print(f"  grounding skip={gr.get('quote_repair_quota_skipped')} fail_open={gr.get('grounding_fail_open')}")
    print(f"  ipc_status={(bi.get('ipc_interpretation') or {}).get('status')} flags={bi.get('health_flags')}")
    print(f"  story={str(bi.get('funnel_story') or '')[:140]}")
