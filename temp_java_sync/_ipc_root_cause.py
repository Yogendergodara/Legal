import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path("outputs")

for name in ["atlassian", "ula", "nda", "eula", "cisco"]:
    p = ROOT / f"{name}_review_live.json"
    if not p.is_file():
        continue
    r = json.loads(p.read_text(encoding="utf-8"))
    findings = r.get("findings") or []
    ipc = [f for f in findings if f.get("status") == "INSUFFICIENT_POLICY_CONTEXT"]
    by_source = Counter((f.get("metadata") or {}).get("source") for f in ipc)
    by_reason = Counter((f.get("metadata") or {}).get("skip_reason") or (f.get("metadata") or {}).get("gap_type") for f in ipc)
    by_section = Counter(str(f.get("contract_section_id") or "?") for f in ipc)
    d = r.get("engine_diagnosis") or {}
    ipc_sum = d.get("ipc_summary") or {}
    print(f"\n{'='*60}\n{name.upper()} IPC={len(ipc)} / {len(findings)} findings")
    print(f"  obligation_ipc_rate={ipc_sum.get('obligation_ipc_rate')} section_ipc_pct={ipc_sum.get('section_ipc_pct')}")
    print(f"  skip_by_reason={ipc_sum.get('skip_by_reason')}")
    print(f"  by_source: {dict(by_source.most_common(8))}")
    print(f"  by_reason: {dict(by_reason.most_common(8))}")
    print(f"  top_sections: {dict(by_section.most_common(6))}")
    samples = []
    for f in ipc[:3]:
        m = f.get("metadata") or {}
        samples.append({
            "sec": f.get("contract_section_id"),
            "source": m.get("source"),
            "reason": m.get("skip_reason") or m.get("gap_type") or m.get("compare_fail_reason", "")[:80],
            "rationale": (f.get("rationale") or "")[:100],
        })
    for s in samples:
        print(f"  sample: {s}")
