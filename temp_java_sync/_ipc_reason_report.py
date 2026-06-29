#!/usr/bin/env python3
"""Print IPC breakdown from a review JSON (OB validation helper)."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from bootstrap_env import load_env, setup_pythonpath

load_env()
setup_pythonpath()

from export_assessment import build_assessment


def report(path: Path) -> None:
    review = json.loads(path.read_text(encoding="utf-8"))
    stats = review.get("compliance_stats") or {}
    assess = build_assessment(review, test_type="ipc_report")
    ipc = Counter()
    for finding in review.get("findings") or []:
        if not isinstance(finding, dict):
            continue
        status = finding.get("status") or finding.get("compliance_status")
        if status != "INSUFFICIENT_POLICY_CONTEXT":
            continue
        src = finding.get("source") or (finding.get("metadata") or {}).get("source") or "unknown"
        gap = finding.get("gap_type") or (finding.get("metadata") or {}).get("gap_type") or ""
        key = f"{src}|{gap}" if gap else str(src)
        ipc[key] += 1

    funnel = (stats.get("obligation_pipeline_funnel") or {})
    print(f"=== IPC report: {path.name} ===")
    print(f"violations={assess['violation_count']} elapsed={review.get('elapsed_seconds')}s")
    print(f"skip_by_reason: {funnel.get('skip_by_reason') or stats.get('obligation_evidence_skip_by_reason')}")
    print(f"compare_queued={funnel.get('compare_queued')} routing_validation_rejected={stats.get('routing_validation_rejected')}")
    print(f"section_skip_count={stats.get('obligation_retrieval_section_skip_count')}")
    print("IPC by source|gap:")
    for key, count in ipc.most_common(20):
        print(f"  {count:4d}  {key}")


def main() -> None:
    root = Path(__file__).resolve().parent / "outputs"
    paths = [Path(p) for p in sys.argv[1:]] if len(sys.argv) > 1 else [root / "atlassian_review_live.json"]
    for path in paths:
        if path.is_file():
            report(path)


if __name__ == "__main__":
    main()
