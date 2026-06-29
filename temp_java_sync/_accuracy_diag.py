import json
from collections import Counter
from pathlib import Path

for name in ["cisco", "atlassian", "ula", "eula", "nda"]:
    p = Path("outputs") / f"{name}_review_live.json"
    if not p.exists():
        continue
    r = json.loads(p.read_text(encoding="utf-8"))
    findings = r.get("findings") or []
    statuses = Counter(f.get("status") for f in findings)
    sources = Counter((f.get("metadata") or {}).get("source") for f in findings)
    d = r.get("engine_diagnosis") or {}
    sec = d.get("section_pipeline") or {}
    res = d.get("resilience") or {}
    st = r.get("compliance_stats") or {}
    warn = r.get("warnings") or []
    nc = statuses.get("NON_COMPLIANT", 0)
    print(f"=== {name} === NC={nc} time={r.get('elapsed_seconds')}s pipeline={d.get('pipeline_mode')}")
    print(f"  statuses: {dict(statuses)}")
    print(f"  top sources: {dict(sources.most_common(5))}")
    print(
        f"  sec_items={sec.get('compare_items')} 429={res.get('llm_rate_limit_events')} "
        f"posture={res.get('llm_review_posture')} sc_failed={st.get('section_compare_failed')}"
    )
    compare_warns = [w for w in warn if "compare" in w.lower()][:2]
    for w in compare_warns:
        print(f"  warn: {w[:120]}")
