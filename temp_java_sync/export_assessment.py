#!/usr/bin/env python3
"""Export a compact assessment JSON from review_result.json (matches Dev UI tabs)."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Stable basenames for known demo/regression titles (Phase F2).
ASSESSMENT_SLUG_OVERRIDES: dict[str, str] = {
    "xecurify": "xecurify_nda",
    "acme corp": "acme_nda",
    "acme corp / cloudvendor": "acme_nda",
}

ROOT = Path(__file__).resolve().parent
OUTPUTS = ROOT / "outputs"

STATUS_RANK = {
    "NON_COMPLIANT": 0,
    "INSUFFICIENT_POLICY_CONTEXT": 1,
    "INCONCLUSIVE": 2,
    "COMPLIANT": 3,
}
SEVERITY_RANK = {"critical": 3, "important": 2, "info": 1}
SCORE_POINTS = {
    "COMPLIANT": 100,
    "INCONCLUSIVE": 60,
    "INSUFFICIENT_POLICY_CONTEXT": 50,
    "NON_COMPLIANT": 0,
}


def _severity_rank(value: str | None) -> int:
    return SEVERITY_RANK.get(str(value or "").lower(), 0)


def _status_rank(value: str | None) -> int:
    return STATUS_RANK.get(str(value or ""), 99)


def primary_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Same dedupe rule as web/app.js primaryFindings()."""
    by_key: dict[str, dict[str, Any]] = {}
    for finding in findings:
        key = f"{finding.get('contract_section_id') or ''}:{finding.get('dimension_label') or ''}"
        existing = by_key.get(key)
        source = (finding.get("metadata") or {}).get("source") or ""
        if existing:
            if source == "playbook_compare" and (existing.get("metadata") or {}).get("source") != "playbook_compare":
                by_key[key] = finding
            continue
        by_key[key] = finding
    return sorted(
        by_key.values(),
        key=lambda f: (
            str(f.get("contract_section_id") or ""),
            -_severity_rank(f.get("severity")),
        ),
    )


def violation_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Same filter as web/app.js renderViolations()."""
    out: list[dict[str, Any]] = []
    for finding in primary_findings(findings):
        if finding.get("status") != "NON_COMPLIANT":
            continue
        if not (finding.get("contract_quote") or finding.get("policy_quote")):
            continue
        if (finding.get("metadata") or {}).get("source") == "section_first_final":
            continue
        out.append(finding)
    return out


def _slim_finding(finding: dict[str, Any]) -> dict[str, Any]:
    meta = finding.get("metadata") or {}
    return {
        "section_id": finding.get("contract_section_id"),
        "dimension": finding.get("dimension_label"),
        "status": finding.get("status"),
        "severity": finding.get("severity"),
        "grounded": finding.get("grounded"),
        "policy_title": meta.get("policy_title"),
        "policy_document_id": finding.get("policy_document_id"),
        "policy_section_id": finding.get("policy_section_id"),
        "source": meta.get("source"),
        "contract_quote": finding.get("contract_quote") or "",
        "policy_quote": finding.get("policy_quote") or "",
        "rationale": finding.get("rationale") or "",
    }


def section_results(primary: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One rolled-up row per contract section (worst status wins)."""
    by_section: dict[str, dict[str, Any]] = {}
    for finding in primary:
        sid = str(finding.get("contract_section_id") or "").strip() or "?"
        current = by_section.get(sid)
        if current is None or _status_rank(finding.get("status")) < _status_rank(current.get("status")):
            by_section[sid] = _slim_finding(finding)
        elif _status_rank(finding.get("status")) == _status_rank(current.get("status")):
            if _severity_rank(finding.get("severity")) > _severity_rank(current.get("severity")):
                by_section[sid] = _slim_finding(finding)
    return [by_section[k] for k in sorted(by_section.keys(), key=lambda x: (len(x), x))]


def compute_scores(section_rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(row.get("status") for row in section_rows)
    total = len(section_rows)
    nc = counts.get("NON_COMPLIANT", 0)
    compliant = counts.get("COMPLIANT", 0)
    weighted = (
        round(sum(SCORE_POINTS.get(str(r.get("status")), 0) for r in section_rows) / total, 1)
        if total
        else 0.0
    )
    return {
        "sections_reviewed": total,
        "compliant_sections": compliant,
        "non_compliant_sections": nc,
        "inconclusive_sections": counts.get("INCONCLUSIVE", 0),
        "insufficient_policy_context_sections": counts.get("INSUFFICIENT_POLICY_CONTEXT", 0),
        "explicit_compliance_rate_pct": round(100 * compliant / total, 1) if total else 0.0,
        "sections_not_non_compliant_pct": round(100 * (total - nc) / total, 1) if total else 0.0,
        "weighted_alignment_score": weighted,
        "status_counts": dict(counts),
    }


def _confidence_from_review(
    review: dict[str, Any],
    primary: list[dict[str, Any]],
    section_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    metadata = (review.get("artifacts") or {}).get("report", {}).get("metadata") or {}
    embedded = (metadata.get("compliance_stats") or {}).get("review_confidence")
    if isinstance(embedded, dict) and embedded:
        return embedded

    total = len(section_rows)
    counts = Counter(row.get("status") for row in section_rows)
    inconclusive = counts.get("INCONCLUSIVE", 0)
    ipc = counts.get("INSUFFICIENT_POLICY_CONTEXT", 0)
    confident = counts.get("COMPLIANT", 0) + counts.get("NON_COMPLIANT", 0)
    return {
        "sections_total": total,
        "inconclusive_section_pct": round(100 * inconclusive / total, 1) if total else 0.0,
        "ipc_section_pct": round(100 * ipc / total, 1) if total else 0.0,
        "confident_section_pct": round(100 * confident / total, 1) if total else 0.0,
        "downgrade_quote_validate": sum(
            1
            for f in primary
            if "Downgraded: model quotes were not exact substrings"
            in str(f.get("rationale") or "")
        ),
        "downgrade_grounding": sum(
            1
            for f in primary
            if (f.get("metadata") or {}).get("grounding_failed") is True
        ),
    }


def build_assessment(
    review: dict[str, Any],
    *,
    sync: dict[str, Any] | None = None,
    test_type: str = "review_export",
    label: str = "",
) -> dict[str, Any]:
    findings = list(review.get("findings") or [])
    primary = primary_findings(findings)
    violations = violation_findings(findings)
    sections = section_results(primary)
    artifact = review.get("artifact") or {}
    ops = artifact.get("ops") or {}
    metadata = (review.get("artifacts") or {}).get("report", {}).get("metadata") or {}

    policies_indexed = len((sync or {}).get("policies") or [])
    if not policies_indexed:
        policies_indexed = len(review.get("discovered_policy_document_ids") or [])

    return {
        "assessment_schema": "temp_java_sync_assessment_v1",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "test_type": test_type,
        "label": label or test_type,
        "tenant_id": (sync or {}).get("tenant_id"),
        "contract_document_id": review.get("contract_document_id"),
        "contract_title": metadata.get("contract_title"),
        "via_platform": review.get("via_platform", False),
        "policy_source": review.get("policy_source"),
        "policies_indexed": policies_indexed,
        "policies_discovered": len(review.get("discovered_policy_document_ids") or []),
        "finding_count_raw": review.get("finding_count") or len(findings),
        "finding_count_primary": len(primary),
        "violation_count": len(violations),
        "scores": compute_scores(sections),
        "confidence": _confidence_from_review(review, primary, sections),
        "summary_markdown": review.get("summary_markdown") or review.get("output") or "",
        "violations": [_slim_finding(v) for v in violations],
        "all_findings": [_slim_finding(f) for f in primary],
        "section_results": sections,
        "indexed_policies": [
            {
                "policy_ref": p.get("policy_ref"),
                "title": p.get("title"),
                "document_id": p.get("document_id"),
                "categories": p.get("categories") or [],
            }
            for p in ((sync or {}).get("policies") or [])
        ],
        "discovered_policy_document_ids": review.get("discovered_policy_document_ids") or [],
        "pipeline": review.get("pipeline"),
        "ops": ops,
        "warnings_sample": list(review.get("warnings") or [])[:20],
        "accuracy_notes": {
            "compare_field": "section_results[].status",
            "gold_label_field": "section_results[].expected_status (add manually or via eval fixture)",
            "match_field": "section_results[].match (set true when actual == expected)",
            "ui_parity": "summary + all_findings + violations match Dev UI tabs",
        },
    }


def assessment_slug(title: str) -> str:
    """Derive a stable basename for named assessment exports."""
    lower = title.lower()
    for needle, slug in ASSESSMENT_SLUG_OVERRIDES.items():
        if needle in lower:
            return slug
    slug = re.sub(r"[^a-z0-9]+", "_", lower).strip("_")
    return slug[:64] if slug else "review"


def export_review_assessments(
    review_path: Path,
    *,
    contract_title: str,
    sync_path: Path | None = None,
    test_type: str = "dev_ui_review",
) -> list[str]:
    """Write latest + named assessment JSON; return output filenames."""
    sync = sync_path if sync_path and sync_path.is_file() else None
    paths: list[str] = []
    latest = export_assessment(
        review_path,
        sync_path=sync,
        out_path=OUTPUTS / "review_assessment.json",
        test_type=test_type,
        label=contract_title,
    )
    paths.append(latest.name)
    slug = assessment_slug(contract_title)
    named = OUTPUTS / f"{slug}_assessment.json"
    if named != latest:
        export_assessment(
            review_path,
            sync_path=sync,
            out_path=named,
            test_type=test_type,
            label=contract_title,
        )
        paths.append(named.name)
    return paths


def export_assessment(
    review_path: Path,
    *,
    sync_path: Path | None = None,
    out_path: Path | None = None,
    test_type: str = "review_export",
    label: str = "",
) -> Path:
    review = json.loads(review_path.read_text(encoding="utf-8"))
    sync = json.loads(sync_path.read_text(encoding="utf-8")) if sync_path and sync_path.is_file() else None
    assessment = build_assessment(review, sync=sync, test_type=test_type, label=label)
    if out_path is None:
        stem = review_path.stem.replace("review_result", "assessment")
        if stem == "assessment":
            stem = "review_assessment"
        out_path = review_path.parent / f"{stem}.json"
    out_path.write_text(json.dumps(assessment, indent=2, ensure_ascii=False), encoding="utf-8")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Export UI-parity assessment JSON from review_result.json")
    parser.add_argument(
        "--review",
        type=Path,
        default=OUTPUTS / "review_result.json",
        help="Path to review_result.json",
    )
    parser.add_argument(
        "--sync",
        type=Path,
        default=OUTPUTS / "sync_result.json",
        help="Path to sync_result.json (optional)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output path (default: outputs/<stem>_assessment.json)",
    )
    parser.add_argument("--test-type", default="review_export")
    parser.add_argument("--label", default="")
    args = parser.parse_args()

    if not args.review.is_file():
        print(f"Missing review file: {args.review}")
        return 1

    out = export_assessment(
        args.review,
        sync_path=args.sync,
        out_path=args.out,
        test_type=args.test_type,
        label=args.label,
    )
    data = json.loads(out.read_text(encoding="utf-8"))
    scores = data.get("scores") or {}
    print(f"Wrote {out}")
    print(
        f"  sections={scores.get('sections_reviewed')} | "
        f"violations={data.get('violation_count')} | "
        f"alignment={scores.get('weighted_alignment_score')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
