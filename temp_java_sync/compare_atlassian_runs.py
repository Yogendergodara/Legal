#!/usr/bin/env python3
"""Compare Atlassian review artifacts against atlassian_v1 baseline."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASELINE = json.loads((ROOT / "baselines" / "atlassian_v1.json").read_text(encoding="utf-8"))
FILES = [
    ROOT / "outputs" / "atlassian_review.json",
    ROOT / "outputs" / "atlassian_review_p5.json",
    ROOT / "outputs" / "atlassian_review_p5_rerun.json",
    ROOT / "outputs" / "atlassian_review_live.json",
]


def extract(path: Path) -> dict | None:
    if not path.is_file():
        return None
    d = json.loads(path.read_text(encoding="utf-8"))
    stats = (d.get("artifact") or {}).get("compliance_stats") or {}
    meta = (d.get("artifacts") or {}).get("report", {}).get("metadata") or {}
    diag = (
        d.get("engine_diagnosis")
        or meta.get("engine_diagnosis")
        or (d.get("artifact") or {}).get("engine_diagnosis")
        or {}
    )
    ipc = diag.get("ipc_summary") or {}
    res = diag.get("resilience") or {}
    funnel = (diag.get("obligation_pipeline") or {}).get("funnel") or stats.get(
        "obligation_pipeline_funnel"
    ) or {}
    findings = d.get("findings") or []
    nc = sum(1 for f in findings if f.get("status") == "NON_COMPLIANT")
    wall = stats.get("review_wall_ms") or stats.get("elapsed_ms")
    return {
        "file": path.name,
        "mtime": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
        "violations_nc": nc,
        "status_counts": dict(Counter(f.get("status") for f in findings)),
        "weighted": stats.get("weighted_alignment_score"),
        "obligation_ipc_rate": ipc.get("obligation_ipc_rate")
        or (stats.get("routing_summary") or {}).get("ipc_rate"),
        "compare_queued": funnel.get("compare_queued"),
        "obligation_compare": stats.get("obligation_compare_count")
        or ipc.get("obligation_compare_count"),
        "llm_batches": funnel.get("llm_batches") or stats.get("obligation_compare_llm_batches"),
        "rate_limit_events": res.get("llm_rate_limit_events") or stats.get("llm_rate_limit_events"),
        "review_wall_ms": wall,
        "node_timings": stats.get("node_timings_ms") or {},
    }


def main() -> None:
    m = BASELINE["metrics"]
    print("=== BASELINE atlassian_v1 ===")
    print(
        f"NC={m['violations_nc']} | ipc={m['obligation_ipc_rate']} | "
        f"queued={m['compare_queued']} | batches={m['obligation_compare_llm_batches']} | "
        f"429s={m['llm_rate_limit_events']} | wall_min={m['review_wall_ms'] / 60000:.1f}"
    )
    rows = [r for p in FILES if (r := extract(p))]
    print()
    for r in rows:
        wall_min = r["review_wall_ms"] / 60000 if r["review_wall_ms"] else None
        print(f"=== {r['file']} ({r['mtime']}) ===")
        print(f"  NC={r['violations_nc']} weighted={r['weighted']}")
        print(
            f"  ipc={r['obligation_ipc_rate']} queued={r['compare_queued']} "
            f"compare={r['obligation_compare']} batches={r['llm_batches']}"
        )
        print(f"  429s={r['rate_limit_events']} wall_min={wall_min}")
        if r["node_timings"]:
            top = sorted(r["node_timings"].items(), key=lambda kv: kv[1], reverse=True)[:5]
            print(f"  top_nodes_ms={top}")


if __name__ == "__main__":
    main()
