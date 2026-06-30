#!/usr/bin/env python3
"""IPC-3 funnel identity check on smoke/review JSON."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from bootstrap_env import load_env, setup_pythonpath

load_env()
setup_pythonpath()

from review_agent.services.ipc3_gates import check_obligation_funnel_identity  # noqa: E402


def _stats_from_review(review: dict) -> dict:
    stats = review.get("compliance_stats") or {}
    if not stats:
        artifact = review.get("artifact") or {}
        if isinstance(artifact, dict):
            stats = artifact.get("compliance_stats") or artifact
    return stats


def check_path(path: Path) -> int:
    review = json.loads(path.read_text(encoding="utf-8"))
    stats = _stats_from_review(review)
    errors = check_obligation_funnel_identity(stats)
    print(f"=== ipc3_funnel_check: {path.name} ===")
    funnel = stats.get("obligation_pipeline_funnel") or {}
    if funnel:
        print(f"extracted={funnel.get('extracted')} queued={funnel.get('compare_queued')} "
              f"pre_ipc={funnel.get('compare_pre_ipc')} llm_ipc={funnel.get('llm_ipc_count')} "
              f"compared={funnel.get('post_validation_compared')}")
    if errors:
        for err in errors:
            print(f"FUNNEL IDENTITY FAIL: {err}", file=sys.stderr)
        return 1
    print("FUNNEL IDENTITY OK")
    return 0


def main() -> int:
    paths = [Path(p) for p in sys.argv[1:]] if len(sys.argv) > 1 else [
        Path(__file__).resolve().parent / "outputs" / "atlassian_pr01_smoke.json"
    ]
    code = 0
    for path in paths:
        if path.is_file():
            code = max(code, check_path(path))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
