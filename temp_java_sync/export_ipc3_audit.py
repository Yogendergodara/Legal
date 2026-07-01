#!/usr/bin/env python3
"""E-BP1 — audit obligation IPC skips and E-BP2 override candidates from review JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _artifact(review: dict) -> dict:
    return review.get("artifact") or review


def _skip_reasons(review: dict) -> dict[str, int]:
    art = _artifact(review)
    stats = art.get("compliance_stats") or {}
    funnel = stats.get("obligation_pipeline_funnel") or {}
    return dict(
        funnel.get("skip_by_reason")
        or stats.get("obligation_evidence_skip_by_reason")
        or {}
    )


def _obligation_rows(review: dict) -> list[dict]:
    art = _artifact(review)
    for key in ("obligation_ipc_rows", "obligation_audit", "obligation_pipeline_rows"):
        rows = art.get(key)
        if isinstance(rows, list) and rows:
            return rows
    return []


def build_audit(review: dict) -> dict:
    art = _artifact(review)
    stats = art.get("compliance_stats") or {}
    rows = _obligation_rows(review)
    skip = _skip_reasons(review)

    bp2_candidates: list[dict] = []
    routing_skips: list[dict] = []
    overlap_skips: list[dict] = []

    for row in rows:
        reason = str(row.get("ipc_reason") or row.get("reason") or "").strip()
        entry = {
            "obligation_id": row.get("obligation_id"),
            "section_id": row.get("section_id"),
            "reason": reason,
            "obligation_type": row.get("obligation_type"),
            "explicit_policy_mentions": row.get("explicit_policy_mentions") or [],
            "is_boilerplate": row.get("is_boilerplate"),
        }
        if reason == "boilerplate":
            otype = str(row.get("obligation_type") or "").lower()
            mentions = row.get("explicit_policy_mentions") or []
            if mentions or (otype and otype not in ("boilerplate", "general")):
                bp2_candidates.append(entry)
        elif reason in ("routing_or_skip", "ipc_preflight"):
            routing_skips.append(entry)
        elif reason == "low_concept_overlap":
            overlap_skips.append(entry)

    return {
        "obligation_total": stats.get("obligation_count") or stats.get("obligation_total"),
        "obligation_ipc_rate": stats.get("obligation_ipc_rate"),
        "compare_queued": (stats.get("obligation_pipeline_funnel") or {}).get("compare_queued"),
        "pre_ipc_reasons": skip,
        "bp2_override_candidates": bp2_candidates,
        "routing_or_skip_rows": routing_skips[:50],
        "low_concept_overlap_rows": overlap_skips[:50],
        "row_count": len(rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="IPC-3 obligation skip audit (E-BP1)")
    parser.add_argument("review_json", type=Path)
    parser.add_argument("-o", "--out", type=Path, default=None)
    args = parser.parse_args()
    if not args.review_json.is_file():
        print(f"Not found: {args.review_json}", file=sys.stderr)
        return 1

    audit = build_audit(json.loads(args.review_json.read_text(encoding="utf-8")))
    out = args.out or args.review_json.with_name(args.review_json.stem + "_ipc_audit.json")
    out.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Wrote {out}")
    print(f"  skip_reasons: {audit.get('pre_ipc_reasons')}")
    print(f"  E-BP2 candidates: {len(audit.get('bp2_override_candidates') or [])}")
    print(f"  routing_or_skip rows: {len(audit.get('routing_or_skip_rows') or [])}")
    print(f"  low_concept_overlap rows: {len(audit.get('low_concept_overlap_rows') or [])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
