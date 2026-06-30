#!/usr/bin/env python3
"""Export obligation IPC audit rows from a smoke/review JSON (E-BP1 / IPC3-0C)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load_review(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def export_audit(review: dict) -> dict:
    artifact = review.get("artifact") or review
    stats = artifact.get("compliance_stats") or {}
    rows = artifact.get("obligation_ipc_rows") or artifact.get("obligation_audit") or []
    pre_ipc = stats.get("obligation_pre_ipc_reasons") or stats.get("pre_ipc_reasons") or {}
    return {
        "obligation_total": stats.get("obligation_total"),
        "obligation_ipc_rate": stats.get("obligation_ipc_rate"),
        "compare_queued": stats.get("compare_queued"),
        "post_validation_compared": stats.get("post_validation_compared"),
        "pre_ipc_reasons": pre_ipc,
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Export IPC-3 obligation audit from review JSON")
    parser.add_argument("review_json", type=Path, help="e.g. outputs/atlassian_pr01_smoke.json")
    parser.add_argument(
        "-o",
        "--out",
        type=Path,
        default=None,
        help="Output path (default: <input>_ipc_audit.json)",
    )
    args = parser.parse_args()
    if not args.review_json.is_file():
        print(f"Not found: {args.review_json}", file=sys.stderr)
        return 1

    audit = export_audit(_load_review(args.review_json))
    out = args.out or args.review_json.with_name(args.review_json.stem + "_ipc_audit.json")
    out.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {out} ({len(audit.get('rows') or [])} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
