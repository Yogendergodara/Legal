#!/usr/bin/env python3
"""Build ipc3_variance_summary.json from variance run artifacts."""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path


def _layer_metrics(review: dict) -> dict:
    stats = review.get("compliance_stats") or {}
    if not stats and review.get("artifact"):
        stats = (review["artifact"] or {}).get("compliance_stats") or review["artifact"]
    funnel = stats.get("obligation_pipeline_funnel") or {}
    diagnosis = review.get("engine_diagnosis") or {}
    ipc = diagnosis.get("ipc_summary") or {}
    skip = funnel.get("skip_by_reason") or stats.get("obligation_evidence_skip_by_reason") or {}
    pre_ipc = int(funnel.get("compare_pre_ipc") or sum(
        v for k, v in skip.items() if k != "evidence_sufficient"
    ))
    extracted = int(funnel.get("extracted") or stats.get("obligation_count") or 0)
    compared = int(funnel.get("post_validation_compared") or stats.get("obligation_compare_count") or 0)
    queued = int(funnel.get("compare_queued") or 0)
    ipc_rate = float(ipc.get("obligation_ipc_rate") or 0)
    if not ipc_rate and extracted:
        ipc_findings = int(stats.get("obligation_ipc_findings") or 0)
        ipc_rate = round(ipc_findings / extracted, 3)
    return {
        "elapsed_seconds": review.get("elapsed_seconds"),
        "extracted": extracted,
        "PRE_IPC": pre_ipc,
        "compare_queued": queued,
        "post_validation_compared": compared,
        "obligation_ipc_rate": ipc_rate,
        "routing_or_skip": int(skip.get("routing_or_skip") or 0),
        "llm_rate_limit_events": int((diagnosis.get("resilience") or {}).get("llm_rate_limit_events") or 0),
        "nc_violations": sum(
            1 for f in review.get("findings") or [] if f.get("status") == "NON_COMPLIANT"
        ),
    }


def _band(values: list[float | int]) -> dict:
    if not values:
        return {"min": None, "max": None, "median": None}
    return {
        "min": min(values),
        "max": max(values),
        "median": round(statistics.median(values), 3) if len(values) > 1 else values[0],
    }


def main() -> int:
    root = Path(__file__).resolve().parent / "outputs"
    paths = [Path(p) for p in sys.argv[1:]] if len(sys.argv) > 1 else [
        root / "ipc3_variance_run_1.json",
        root / "ipc3_variance_run_2.json",
        root / "ipc3_variance_run_3.json",
    ]
    runs = []
    metrics_list = []
    for path in paths:
        if not path.is_file():
            print(f"missing {path}", file=sys.stderr)
            return 1
        review = json.loads(path.read_text(encoding="utf-8"))
        m = _layer_metrics(review)
        m["artifact"] = path.name
        metrics_list.append(m)
        runs.append(path.name)

    keys = [
        "PRE_IPC", "compare_queued", "post_validation_compared",
        "obligation_ipc_rate", "routing_or_skip", "llm_rate_limit_events", "nc_violations",
    ]
    summary = {
        "schema_version": "ipc3_variance_v1",
        "runs": runs,
        "per_run": metrics_list,
        "band": {key: _band([m[key] for m in metrics_list if m.get(key) is not None]) for key in keys},
        "frozen_config": {
            "obligation_compare_prompt": "v1",
            "ipc3_boilerplate_substantive_override_enabled": False,
            "evidence_semantic_overlap_enabled": False,
        },
    }
    out = root / "ipc3_variance_summary.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Wrote {out}")
    print(json.dumps(summary["band"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
