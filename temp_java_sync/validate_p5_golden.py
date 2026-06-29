#!/usr/bin/env python3
"""P5 golden validation — Cisco, Atlassian, ULA, EULA (structural + regression gates)."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

from bootstrap_env import apply_golden_review_defaults, load_env, setup_pythonpath

load_env()
os.environ.setdefault("GOLDEN_LLM_PROFILE_FORCE", "true")
apply_golden_review_defaults()
setup_pythonpath()

from beta_test.benchmark_score import findings_by_section_from_report, score_section_expected, specs_from_legacy_expected
from document_core.schemas.compliance import ReviewReport
from e2e_harness import contract_fixture_to_text, policy_fixture_to_sync, review_text, sync_policies
from export_assessment import build_assessment, violation_findings, primary_findings

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs"
THRESHOLDS_PATH = ROOT / "golden_thresholds.json"


def _load_thresholds() -> dict[str, Any]:
    if THRESHOLDS_PATH.is_file():
        return json.loads(THRESHOLDS_PATH.read_text(encoding="utf-8"))
    return {}


async def _assert_routing_golden() -> None:
    from review_agent.services.routing_golden_harness import run_all_golden_cases, wrong_policy_compare_count

    results = await run_all_golden_cases()
    count = wrong_policy_compare_count(results)
    if count != 0:
        offenders = [r.obligation_id for r in results if r.forbidden_violations]
        raise AssertionError(f"wrong_policy_compare_count={count}, offenders={offenders}")


def _assert_ipc_thresholds(name: str, diagnosis: dict[str, Any]) -> None:
    thresholds = _load_thresholds().get(name) or {}
    ipc_summary = diagnosis.get("ipc_summary") or {}
    max_obligation_rate = thresholds.get("max_obligation_ipc_rate")
    if max_obligation_rate is not None:
        ipc_rate = ipc_summary.get("obligation_ipc_rate")
        if ipc_rate is not None and float(ipc_rate) > float(max_obligation_rate):
            raise AssertionError(
                f"{name}: obligation_ipc_rate {ipc_rate} > ceiling {max_obligation_rate}"
            )
    max_section_ipc = thresholds.get("max_section_ipc_pct")
    if max_section_ipc is not None:
        section_ipc = ipc_summary.get("section_ipc_pct")
        if section_ipc is not None and float(section_ipc) > float(max_section_ipc):
            raise AssertionError(
                f"{name}: section_ipc_pct {section_ipc} > ceiling {max_section_ipc}"
            )


def _assert_discovery_scope(name: str, diagnosis: dict[str, Any]) -> None:
    thresholds = _load_thresholds().get(name) or {}
    max_discovered = thresholds.get("max_policies_discovered")
    if max_discovered is None:
        return
    discovery = diagnosis.get("discovery") or {}
    ipc_summary = diagnosis.get("ipc_summary") or {}
    discovered = discovery.get("policies_discovered")
    if discovered is None:
        discovered = ipc_summary.get("policies_discovered")
    if discovered is not None and int(discovered) > int(max_discovered):
        raise AssertionError(
            f"{name}: policies_discovered {discovered} > ceiling {max_discovered}"
        )


def _assert_obligation_cap(name: str, diagnosis: dict[str, Any]) -> None:
    thresholds = _load_thresholds().get(name) or {}
    max_extracted = thresholds.get("max_obligations_extracted")
    min_cap_dropped = thresholds.get("min_obligation_cap_dropped")

    extract_cap = (diagnosis.get("obligation_pipeline") or {}).get("extract_cap") or {}
    post_cap = extract_cap.get("post_cap_count")
    dropped = extract_cap.get("dropped_count")
    funnel = (diagnosis.get("obligation_pipeline") or {}).get("funnel") or {}
    extracted = funnel.get("extracted")

    if max_extracted is not None:
        actual = post_cap if post_cap is not None else extracted
        if actual is not None and int(actual) > int(max_extracted):
            raise AssertionError(
                f"{name}: obligations_extracted {actual} > ceiling {max_extracted}"
            )

    if min_cap_dropped is not None:
        raw_extract = int(extracted or post_cap or 0)
        if raw_extract > 80 and int(dropped or 0) < int(min_cap_dropped):
            raise AssertionError(
                f"{name}: obligation_cap_dropped {dropped or 0} < floor {min_cap_dropped} "
                f"(extracted={raw_extract})"
            )


def _assert_recovery_thresholds(name: str, diagnosis: dict[str, Any]) -> None:
    thresholds = _load_thresholds().get(name) or {}
    recovery = diagnosis.get("recovery") or {}
    gap_summary = recovery.get("gap_status_summary") or {}
    accuracy_paths = diagnosis.get("accuracy_paths") or {}
    recover = accuracy_paths.get("recover") or {}
    final_verify = recovery.get("final_verify") or {}

    min_recovered = thresholds.get("min_compare_omitted_recovered")
    if min_recovered is not None:
        recovered = recover.get("compare_omitted_recovered")
        if recovered is None:
            recovered = gap_summary.get("compare_omitted_recovered")
        if recovered is None:
            recovered = final_verify.get("compare_omitted_recovered")
        if recovered is not None and int(recovered) < int(min_recovered):
            raise AssertionError(
                f"{name}: compare_omitted_recovered {recovered} < floor {min_recovered}"
            )

    min_gap_sections = thresholds.get("min_gap_sections")
    if min_gap_sections is not None:
        gap_sections = recover.get("gap_sections")
        if gap_sections is None:
            gap_sections = gap_summary.get("gap_sections")
        if gap_sections is None:
            gap_sections = final_verify.get("gap_sections")
        if gap_sections is not None and int(gap_sections) < int(min_gap_sections):
            raise AssertionError(
                f"{name}: gap_sections {gap_sections} < floor {min_gap_sections}"
            )


def _assert_extract_quality(name: str, diagnosis: dict[str, Any]) -> None:
    thresholds = _load_thresholds().get(name) or {}
    min_llm_rate = thresholds.get("min_llm_extract_rate")
    max_fallback = thresholds.get("max_extract_fallback_count")
    if min_llm_rate is None and max_fallback is None:
        return
    extract_quality = (diagnosis.get("obligation_pipeline") or {}).get("extract_quality") or {}
    if max_fallback is not None:
        fallback = int(extract_quality.get("fallback_count") or 0)
        if fallback > int(max_fallback):
            raise AssertionError(
                f"{name}: extract_fallback_count {fallback} > ceiling {max_fallback}"
            )
    if min_llm_rate is not None:
        llm_rate = extract_quality.get("llm_extract_rate")
        if llm_rate is not None and float(llm_rate) < float(min_llm_rate):
            raise AssertionError(
                f"{name}: llm_extract_rate {llm_rate} < floor {min_llm_rate}"
            )


def _assert_section_status_floors(
    name: str,
    assessment: dict[str, Any],
    diagnosis: dict[str, Any],
) -> None:
    thresholds = _load_thresholds().get(name) or {}
    floors = thresholds.get("section_status_floors")
    if not floors:
        return
    min_compare = int(thresholds.get("min_section_floor_compare_items") or 20)
    compare_items = int((diagnosis.get("section_pipeline") or {}).get("compare_items") or 0)
    if not compare_items:
        compare_items = int((diagnosis.get("compliance_stats") or {}).get("compare_items") or 0)
    if compare_items < min_compare:
        return
    status_rank = {
        "NON_COMPLIANT": 0,
        "INSUFFICIENT_POLICY_CONTEXT": 1,
        "INCONCLUSIVE": 2,
        "COMPLIANT": 3,
    }
    by_section = {
        str(row.get("section_id") or ""): str(row.get("status") or "")
        for row in assessment.get("section_results") or []
    }
    for section_id, min_status in floors.items():
        actual = by_section.get(str(section_id))
        if not actual:
            continue
        floor_rank = status_rank.get(str(min_status), 99)
        actual_rank = status_rank.get(actual, 99)
        if actual_rank > floor_rank:
            raise AssertionError(
                f"{name}: section {section_id} status {actual} worse than floor {min_status}"
            )


def _section_compare_metrics(diagnosis: dict[str, Any]) -> tuple[int | None, int | None]:
    section_pipeline = diagnosis.get("section_pipeline") or {}
    infra = (diagnosis.get("infrastructure") or {}).get("section_compare_batches") or {}
    compare_items = section_pipeline.get("compare_items")
    if compare_items is None:
        compare_items = section_pipeline.get("sections_compared")
    batches = infra.get("actual")
    if batches is None:
        batches = section_pipeline.get("llm_batches_actual")
    compare_int = int(compare_items) if compare_items is not None else None
    batch_int = int(batches) if batches is not None else None
    return compare_int, batch_int


def _review_wall_ms(review: dict[str, Any] | None) -> int | None:
    if not review:
        return None
    stats = review.get("compliance_stats") or {}
    if not stats:
        artifact = review.get("artifact") or {}
        stats = artifact.get("compliance_stats") or {}
    wall = stats.get("review_wall_ms") or stats.get("elapsed_ms")
    if wall is None:
        elapsed = review.get("elapsed_seconds")
        if elapsed is not None:
            return int(float(elapsed) * 1000)
    return int(wall) if wall is not None else None


def _assert_section_compare_floors(name: str, diagnosis: dict[str, Any]) -> None:
    thresholds = _load_thresholds().get(name) or {}
    min_items = thresholds.get("min_compare_items")
    min_batches = thresholds.get("min_section_compare_batches")
    if min_items is None and min_batches is None:
        return
    compare_items, batches = _section_compare_metrics(diagnosis)
    if min_items is not None and compare_items is not None and compare_items < int(min_items):
        raise AssertionError(
            f"{name}: compare_items {compare_items} < floor {min_items}"
        )
    if min_batches is not None and batches is not None and batches < int(min_batches):
        raise AssertionError(
            f"{name}: section_compare_batches {batches} < floor {min_batches}"
        )


def _assert_cisco_legal_score(name: str, review: dict[str, Any]) -> None:
    if name != "cisco":
        return
    thresholds = _load_thresholds().get(name) or {}
    min_score = thresholds.get("min_legal_score_10")
    min_hits = thresholds.get("min_section_score_hits")
    if min_score is None and min_hits is None:
        return
    by_section = _findings_by_section(review)
    specs = specs_from_legacy_expected(CISCO_EXPECTED)
    hits, _, score = score_section_expected(by_section, specs)
    if min_score is not None and float(score) < float(min_score):
        raise AssertionError(
            f"{name}: legal_score_10 {score} < floor {min_score}"
        )
    if min_hits is not None and int(hits) < int(min_hits):
        raise AssertionError(
            f"{name}: section_score_hits {hits} < floor {min_hits}"
        )


def _assert_wall_time_sanity(
    name: str,
    diagnosis: dict[str, Any],
    assessment: dict[str, Any],
    review: dict[str, Any] | None = None,
) -> None:
    thresholds = _load_thresholds().get(name) or {}
    min_wall = thresholds.get("min_review_wall_ms")
    min_violations = thresholds.get("min_violations")
    if min_wall is None or not min_violations:
        return
    nc = int(assessment.get("violation_count") or 0)
    if nc >= int(min_violations):
        return
    wall_ms = _review_wall_ms(review)
    if wall_ms is None:
        return
    if wall_ms < int(min_wall):
        raise AssertionError(
            f"{name}: review_wall_ms {wall_ms} < floor {min_wall} with violations {nc} < {min_violations}"
        )


def _assert_golden_gates(
    name: str,
    diagnosis: dict[str, Any],
    assessment: dict[str, Any] | None = None,
    review: dict[str, Any] | None = None,
) -> None:
    _assert_ipc_thresholds(name, diagnosis)
    _assert_baseline_thresholds(name, diagnosis)
    _assert_discovery_scope(name, diagnosis)
    _assert_obligation_cap(name, diagnosis)
    _assert_recovery_thresholds(name, diagnosis)
    _assert_extract_quality(name, diagnosis)
    _assert_section_compare_floors(name, diagnosis)
    if review is not None:
        _assert_cisco_legal_score(name, review)
    if assessment is not None:
        _assert_section_status_floors(name, assessment, diagnosis)
        _assert_wall_time_sanity(name, diagnosis, assessment, review)


def _assert_baseline_thresholds(name: str, diagnosis: dict[str, Any]) -> None:
    """Phase G — funnel + rate-limit regression bands (violations checked separately)."""
    thresholds = _load_thresholds().get(name) or {}
    if not thresholds:
        return

    resilience = diagnosis.get("resilience") or {}
    funnel = (diagnosis.get("obligation_pipeline") or {}).get("funnel") or {}

    max_rate_limit = thresholds.get("max_llm_rate_limit_events")
    if max_rate_limit is not None:
        events = int(resilience.get("llm_rate_limit_events") or 0)
        if events > int(max_rate_limit):
            raise AssertionError(
                f"{name}: llm_rate_limit_events {events} > ceiling {max_rate_limit}"
            )

    min_compare_queued = thresholds.get("min_compare_queued")
    if min_compare_queued is not None:
        compare_queued = funnel.get("compare_queued")
        if compare_queued is not None and int(compare_queued) < int(min_compare_queued):
            raise AssertionError(
                f"{name}: compare_queued {compare_queued} < floor {min_compare_queued}"
            )

    min_batches = thresholds.get("min_obligation_compare_batches")
    if min_batches is not None:
        batches = funnel.get("llm_batches")
        if batches is not None and int(batches) < int(min_batches):
            raise AssertionError(
                f"{name}: obligation_compare_llm_batches {batches} < floor {min_batches}"
            )

CISCO_EXPECTED = {
    "1": {
        "topic": "Supplier Code of Conduct (RBA Silver)",
        "expect": {"NON_COMPLIANT", "INCONCLUSIVE"},
    },
    "2": {"topic": "Human Rights / Forced Labor", "expect": {"NON_COMPLIANT"}},
    "3": {
        "topic": "Responsible Minerals (MRT/RMAP)",
        "expect": {"NON_COMPLIANT", "INCONCLUSIVE"},
    },
    "4": {"topic": "Environment / CDP / GHG", "expect": {"NON_COMPLIANT", "INCONCLUSIVE"}},
    "5": {"topic": "Security (MSS)", "expect": {"NON_COMPLIANT"}},
    "6": {"topic": "Risk / SCV / BCP", "expect": {"NON_COMPLIANT", "INCONCLUSIVE"}},
}


def _assert_engine_diagnosis(review: dict[str, Any], label: str) -> dict[str, Any]:
    meta = (review.get("artifacts") or {}).get("report", {}).get("metadata") or {}
    diagnosis = review.get("engine_diagnosis") or meta.get("engine_diagnosis") or {}
    artifact = review.get("artifact") or {}
    if not diagnosis:
        raise AssertionError(f"{label}: engine_diagnosis missing")
    if diagnosis != meta.get("engine_diagnosis"):
        raise AssertionError(f"{label}: metadata.engine_diagnosis mismatch")
    if diagnosis != artifact.get("engine_diagnosis"):
        raise AssertionError(f"{label}: artifact.engine_diagnosis mismatch")
    if diagnosis.get("schema_version") != "1.0":
        raise AssertionError(f"{label}: unexpected schema_version {diagnosis.get('schema_version')}")
    return diagnosis


def _findings_by_section(review: dict[str, Any]) -> dict[str, dict]:
    report = ReviewReport.model_validate((review.get("artifacts") or {}).get("report") or {})
    return findings_by_section_from_report(report)


async def run_cisco(http: httpx.AsyncClient) -> dict[str, Any]:
    tenant = "cisco-beta"
    contract = json.loads((ROOT / "fixtures" / "cisco" / "acme_hardware_supplier_agreement.json").read_text())
    policies = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted((ROOT / "fixtures" / "cisco" / "policies").glob("*.json"))
    ]
    await sync_policies(
        http,
        [policy_fixture_to_sync(p) for p in policies],
        tenant_id=tenant,
    )
    started = time.time()
    review = await review_text(
        http,
        contract_text=contract_fixture_to_text(contract),
        contract_title=contract.get("title") or "Acme Hardware Supply Agreement",
        contract_type=contract.get("contract_type") or "vendor",
        query="Review this hardware supplier agreement against Cisco supplier policies",
        tenant_id=tenant,
        use_platform=False,
    )
    elapsed = round(time.time() - started, 1)
    diagnosis = _assert_engine_diagnosis(review, "Cisco")
    if diagnosis.get("pipeline_mode") != "section_first":
        raise AssertionError(f"Cisco: expected section_first, got {diagnosis.get('pipeline_mode')}")

    by_section = _findings_by_section(review)
    specs = specs_from_legacy_expected(CISCO_EXPECTED)
    hits, section_results, score = score_section_expected(by_section, specs)
    violations = violation_findings(review.get("findings") or [])
    result = {
        "test": "cisco",
        "elapsed_seconds": elapsed,
        "legal_score_10": score,
        "gate_passed": score >= 10.0,
        "section_hits": hits,
        "section_results": section_results,
        "violations_with_quotes": len(violations),
        "engine_diagnosis": diagnosis,
        "pipeline_mode": diagnosis.get("pipeline_mode"),
    }
    OUT.mkdir(exist_ok=True)
    (OUT / "cisco_review_p5.json").write_text(json.dumps(review, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT / "cisco_assessment_p5.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    min_score = float((_load_thresholds().get("cisco") or {}).get("min_legal_score_10") or 6.0)
    if score < min_score:
        raise AssertionError(f"Cisco gate failed: score {score} < {min_score}")
    _assert_golden_gates("cisco", diagnosis, review=review)
    return result


async def run_named_review(
    http: httpx.AsyncClient,
    *,
    name: str,
    fixture_path: Path,
    contract_path: Path,
    contract_title: str,
    contract_type: str,
    query: str,
    tenant: str,
    min_violations: int,
    min_weighted: float | None = None,
) -> dict[str, Any]:
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    policies = data["policies"]
    contract_text = contract_path.read_text(encoding="utf-8")
    await sync_policies(http, policies, tenant_id=tenant)
    started = time.time()
    review = await review_text(
        http,
        contract_text=contract_text,
        contract_title=contract_title,
        contract_type=contract_type,
        query=query,
        tenant_id=tenant,
        use_platform=False,
    )
    elapsed = round(time.time() - started, 1)
    diagnosis = _assert_engine_diagnosis(review, name)
    assessment = build_assessment(review, test_type=f"p5_{name}", label=contract_title)
    violations = assessment["violation_count"]
    weighted = assessment["scores"]["weighted_alignment_score"]
    out_name = f"{name}_review_p5.json"
    (OUT / out_name).write_text(json.dumps(review, indent=2, ensure_ascii=False), encoding="utf-8")
    if violations < min_violations:
        raise AssertionError(f"{name}: violations {violations} < baseline {min_violations}")
    if min_weighted is not None and weighted < min_weighted - 5:
        raise AssertionError(f"{name}: weighted score {weighted} dropped >5 pts from {min_weighted}")
    _assert_golden_gates(name, diagnosis, assessment, review=review)
    return {
        "test": name,
        "elapsed_seconds": elapsed,
        "violation_count": violations,
        "weighted_alignment_score": weighted,
        "pipeline_mode": diagnosis.get("pipeline_mode"),
        "ipc_summary": diagnosis.get("ipc_summary"),
        "status_counts": Counter(f.get("status") for f in review.get("findings") or []),
    }


async def run_nda(http: httpx.AsyncClient) -> dict[str, Any]:
    data = json.loads((ROOT / "fixtures" / "xecurify_e2e.json").read_text(encoding="utf-8"))
    tenant = data.get("tenant_id", "e2e-demo")
    await sync_policies(http, data["policies"], tenant_id=tenant)
    started = time.time()
    review = await review_text(
        http,
        contract_text=data["contract_text"],
        contract_title="Mutual NDA - Xecurify / Recipient",
        contract_type="nda",
        query=(
            "Review this mutual NDA against our Code of Conduct, data retention, "
            "security, and privacy policies"
        ),
        tenant_id=tenant,
        use_platform=False,
    )
    elapsed = round(time.time() - started, 1)
    diagnosis = _assert_engine_diagnosis(review, "nda")
    assessment = build_assessment(review, test_type="p5_nda", label="Mutual NDA - Xecurify / Recipient")
    violations = assessment["violation_count"]
    min_violations = int((_load_thresholds().get("nda") or {}).get("min_violations", 1))
    OUT.mkdir(exist_ok=True)
    (OUT / "nda_review_p5.json").write_text(json.dumps(review, indent=2, ensure_ascii=False), encoding="utf-8")
    if violations < min_violations:
        raise AssertionError(f"nda: violations {violations} < baseline {min_violations}")
    _assert_golden_gates("nda", diagnosis)
    return {
        "test": "nda",
        "elapsed_seconds": elapsed,
        "violation_count": violations,
        "weighted_alignment_score": assessment["scores"]["weighted_alignment_score"],
        "pipeline_mode": diagnosis.get("pipeline_mode"),
        "ipc_summary": diagnosis.get("ipc_summary"),
        "status_counts": Counter(f.get("status") for f in review.get("findings") or []),
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--only",
        choices=["cisco", "atlassian", "ula", "eula", "nda", "all"],
        default="all",
    )
    args = parser.parse_args()
    targets = {"cisco", "atlassian", "ula", "eula", "nda"} if args.only == "all" else {args.only}
    results: list[dict[str, Any]] = []

    print("=== Routing golden (unit gate) ===")
    await _assert_routing_golden()
    print("routing golden: wrong_policy_compare_count=0")

    async with httpx.AsyncClient(timeout=httpx.Timeout(7200.0)) as http:
        health = await http.get("http://localhost:8090/api/health")
        health.raise_for_status()

        if "cisco" in targets:
            print("=== Cisco ===")
            results.append(await run_cisco(http))

        if "atlassian" in targets:
            print("=== Atlassian ===")
            results.append(
                await run_named_review(
                    http,
                    name="atlassian",
                    fixture_path=ROOT / "fixtures" / "atlassian_e2e.json",
                    contract_path=ROOT / "fixtures" / "atlassian_customer_agreement.txt",
                    contract_title="Atlassian Customer Agreement",
                    contract_type="saas",
                    query=(
                        "Review this Atlassian Customer Agreement against all indexed Atlassian policies"
                    ),
                    tenant="e2e-demo",
                    min_violations=6,
                )
            )

        if "ula" in targets:
            print("=== ULA ===")
            results.append(
                await run_named_review(
                    http,
                    name="ula",
                    fixture_path=ROOT / "fixtures" / "xecurify_e2e.json",
                    contract_path=ROOT / "fixtures" / "xecurify_ula_contract.txt",
                    contract_title="User License Agreement - Xecurify / Customer",
                    contract_type="saas",
                    query=(
                        "Review this Xecurify user license agreement against our security, "
                        "privacy, data retention, incident response, and code of conduct policies"
                    ),
                    tenant="e2e-demo",
                    min_violations=2,
                )
            )

        if "eula" in targets:
            print("=== EULA ===")
            results.append(
                await run_named_review(
                    http,
                    name="eula",
                    fixture_path=ROOT / "fixtures" / "xecurify_e2e.json",
                    contract_path=ROOT / "fixtures" / "xecurify_plugin_eula.txt",
                    contract_title="End User License Agreement - Xecurify Plugin",
                    contract_type="saas",
                    query=(
                        "Review this Xecurify plugin end user license agreement against our "
                        "privacy policy, security practices, data retention, incident response, "
                        "and code of conduct policies"
                    ),
                    tenant="e2e-demo",
                    min_violations=3,
                    min_weighted=39.2,
                )
            )

        if "nda" in targets:
            print("=== NDA ===")
            results.append(await run_nda(http))

    summary_path = OUT / "p5_golden_validation.json"
    summary_path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(json.dumps(results, indent=2, default=str))
    print(f"Wrote {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
