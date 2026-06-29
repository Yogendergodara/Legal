"""Phase G — baseline interpretation for known IPC / LLM funnel metrics."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_BASELINES_DIR = Path(__file__).resolve().parent.parent / "data" / "baselines"


def _int_val(data: dict[str, Any], key: str, default: int = 0) -> int:
    raw = data.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _float_val(data: dict[str, Any], key: str, default: float | None = None) -> float | None:
    raw = data.get(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def load_baseline_profile(profile_id: str) -> dict[str, Any] | None:
    """Load a committed baseline snapshot by profile id (e.g. atlassian_v1)."""
    profile_id = str(profile_id or "").strip()
    if not profile_id:
        return None

    candidates: list[Path] = []
    data_dir = str(__import__("os").environ.get("BASELINE_DATA_DIR") or "").strip()
    if data_dir:
        candidates.append(Path(data_dir) / f"{profile_id}.json")
    candidates.append(_BASELINES_DIR / f"{profile_id}.json")

    for path in candidates:
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _extract_actuals(diagnosis: dict[str, Any], compliance_stats: dict[str, Any]) -> dict[str, Any]:
    ipc_summary = dict(diagnosis.get("ipc_summary") or {})
    resilience = dict(diagnosis.get("resilience") or {})
    obligation_pipeline = dict(diagnosis.get("obligation_pipeline") or {})
    funnel = dict(obligation_pipeline.get("funnel") or {})
    routing_summary = dict(obligation_pipeline.get("routing_summary") or {})
    if not routing_summary:
        routing_summary = dict(compliance_stats.get("routing_summary") or {})

    obligations_extracted = _int_val(funnel, "extracted")
    if not obligations_extracted:
        obligations_extracted = _int_val(routing_summary, "obligation_count")

    compare_queued = _int_val(funnel, "compare_queued")
    batches = _int_val(compliance_stats, "obligation_compare_llm_batches")
    if not batches:
        batches = _int_val(funnel, "llm_batches")

    ipc_rate = _float_val(ipc_summary, "obligation_ipc_rate")
    if ipc_rate is None:
        ipc_rate = _float_val(routing_summary, "ipc_rate")

    planner_calls = routing_summary.get("planner_calls")
    if planner_calls is None:
        planner_calls = compliance_stats.get("routing_planner_calls")
    try:
        planner_calls_int = int(planner_calls or 0)
    except (TypeError, ValueError):
        planner_calls_int = 0

    violations_nc = _int_val(compliance_stats, "non_compliant_count")
    if not violations_nc:
        status_counts = compliance_stats.get("status_counts") or {}
        if isinstance(status_counts, dict):
            violations_nc = _int_val(status_counts, "NON_COMPLIANT")

    review_wall_ms = _int_val(compliance_stats, "review_wall_ms")
    if not review_wall_ms:
        review_wall_ms = _int_val(compliance_stats, "elapsed_ms")

    return {
        "obligations_extracted": obligations_extracted,
        "compare_queued": compare_queued,
        "obligation_compare_llm_batches": batches,
        "obligation_ipc_findings": _int_val(ipc_summary, "obligation_ipc_findings"),
        "obligation_compare_count": _int_val(ipc_summary, "obligation_compare_count"),
        "obligation_ipc_rate": ipc_rate,
        "llm_rate_limit_events": _int_val(resilience, "llm_rate_limit_events"),
        "violations_nc": violations_nc,
        "routing_planner_calls": planner_calls_int,
        "review_wall_ms": review_wall_ms,
        "skip_by_reason": dict(ipc_summary.get("skip_by_reason") or {}),
    }


def _build_funnel_story(
    actuals: dict[str, Any],
    *,
    compare_items: int | None = None,
    wall_min: float | None = None,
    ipc_rate: float | None = None,
) -> str:
    extracted = actuals.get("obligations_extracted") or "?"
    queued = actuals.get("compare_queued") or "?"
    batches = actuals.get("obligation_compare_llm_batches") or "?"
    nc = actuals.get("violations_nc") or "?"
    story = f"{extracted} extracted → {queued} evidence-compare → {batches} LLM batches → {nc} NC"
    if compare_items is not None:
        story += f" | section_compare_items={compare_items}"
    if ipc_rate is not None:
        story += f" | ipc={ipc_rate:.2f}"
    if wall_min is not None:
        story += f" | wall={wall_min:.1f}min"
    return story


def _ipc_interpretation_status(
    *,
    pathological: bool,
    violations_nc: int,
    baseline_min: int,
    queued_actual: int,
    queued_baseline: int,
    batches_actual: int,
    batches_baseline: int,
    ipc_actual: float | None,
) -> str:
    if pathological:
        return "pathological"
    funnel_ok = bool(
        baseline_min
        and violations_nc >= baseline_min
        and queued_baseline
        and queued_actual >= int(queued_baseline * 0.5)
        and batches_baseline
        and batches_actual >= int(batches_baseline * 0.5)
    )
    if funnel_ok:
        if ipc_actual is not None and 0.50 <= float(ipc_actual) <= 0.65:
            return "aspirational_progress"
        return "evidence_healthy"
    return "neutral"


def _delta_status_lower_better(actual: int | float, baseline: int | float) -> str:
    if actual < baseline:
        return "improved"
    if actual > baseline * 1.25:
        return "elevated"
    return "ok"


def _delta_status_higher_better(actual: int | float, baseline: int | float) -> str:
    if actual >= baseline:
        return "ok"
    if actual < baseline * 0.75:
        return "low"
    return "ok"


def _metric_delta(
    *,
    actual: int | float | None,
    baseline: int | float | None,
    status: str,
    delta: float | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "actual": actual,
        "baseline": baseline,
        "status": status,
    }
    if delta is not None:
        out["delta"] = round(delta, 3)
    return out


def has_accuracy_regression(interpretation: dict[str, Any] | None) -> bool:
    if not interpretation:
        return False
    if "accuracy_regression" in (interpretation.get("health_flags") or []):
        return True
    return (interpretation.get("primary_accuracy") or {}).get("status") == "regression"


def build_baseline_interpretation(
    diagnosis: dict[str, Any],
    compliance_stats: dict[str, Any],
    *,
    baseline: dict[str, Any],
) -> dict[str, Any]:
    """Compare live diagnosis against a committed baseline snapshot."""
    baseline_id = str(baseline.get("baseline_id") or "")
    baseline_metrics = dict(baseline.get("metrics") or {})
    baseline_min = _int_val(baseline, "min_violations")
    if not baseline_min:
        baseline_min = _int_val(baseline_metrics, "violations_nc")

    actuals = _extract_actuals(diagnosis, compliance_stats)
    health_flags: list[str] = []

    violations_nc = actuals["violations_nc"]
    if baseline_min and violations_nc and violations_nc < baseline_min:
        health_flags.append("accuracy_regression")
        accuracy_status = "regression"
    elif baseline_min and violations_nc >= baseline_min:
        accuracy_status = "ok"
    else:
        accuracy_status = "unknown"

    ipc_actual = actuals.get("obligation_ipc_rate")
    ipc_baseline = _float_val(baseline_metrics, "obligation_ipc_rate")
    ipc_delta = None
    ipc_status = "ok"
    if ipc_actual is not None and ipc_baseline is not None:
        ipc_delta = ipc_actual - ipc_baseline
        if ipc_delta > 0.10 and accuracy_status == "ok":
            health_flags.append("ipc_drift_review")
            ipc_status = "drift"
        elif ipc_actual >= 0.75:
            health_flags.append("ipc_expected_high")

    rate_actual = actuals["llm_rate_limit_events"]
    rate_baseline = _int_val(baseline_metrics, "llm_rate_limit_events")
    rate_status = _delta_status_lower_better(rate_actual, rate_baseline) if rate_baseline else "ok"
    if rate_status == "elevated":
        health_flags.append("rate_limit_elevated")

    batches_actual = actuals["obligation_compare_llm_batches"]
    batches_baseline = _int_val(baseline_metrics, "obligation_compare_llm_batches")
    batches_status = (
        _delta_status_higher_better(batches_actual, batches_baseline) if batches_baseline else "ok"
    )

    queued_actual = actuals["compare_queued"]
    queued_baseline = _int_val(baseline_metrics, "compare_queued")
    if (
        queued_baseline
        and queued_actual >= int(queued_baseline * 1.15)
        and batches_baseline
        and batches_actual <= int(batches_baseline * 1.10)
    ):
        health_flags.append("compare_funnel_stuck")

    extracted = actuals.get("obligations_extracted") or 0
    if (
        extracted >= 20
        and queued_actual == 0
        and ipc_actual is not None
        and float(ipc_actual) >= 0.85
    ):
        health_flags.append("pathological_ipc_funnel")

    if (
        baseline_min
        and violations_nc
        and violations_nc < baseline_min
        and "pathological_ipc_funnel" not in health_flags
        and ipc_actual is not None
        and float(ipc_actual) >= 0.85
    ):
        health_flags.append("section_nc_regression")

    recovery = dict(diagnosis.get("recovery") or {})
    gap_summary = dict(recovery.get("gap_status_summary") or {})
    accuracy_paths = dict(diagnosis.get("accuracy_paths") or {})
    if accuracy_paths.get("recovery_starved"):
        health_flags.append("f5_recovery_starved")
    else:
        recover = dict(accuracy_paths.get("recover") or {})
        final_verify = dict(recovery.get("final_verify") or {})
        gap_sections = _int_val(gap_summary, "gap_sections") or _int_val(recover, "gap_sections")
        if not gap_sections:
            gap_sections = _int_val(final_verify, "gap_sections")
        compare_omitted_recovered = _int_val(gap_summary, "compare_omitted_recovered")
        if not compare_omitted_recovered:
            compare_omitted_recovered = _int_val(recover, "compare_omitted_recovered")
        compare_omitted_eligible = _int_val(gap_summary, "compare_omitted_eligible")
        if not compare_omitted_eligible:
            compare_omitted_eligible = _int_val(compliance_stats, "recovery_compare_omitted_eligible")
        if (
            gap_sections >= 5
            and compare_omitted_recovered == 0
            and compare_omitted_eligible > 0
        ):
            health_flags.append("f5_recovery_starved")

    discovery = dict(diagnosis.get("discovery") or {})
    ipc_summary = dict(diagnosis.get("ipc_summary") or {})
    compare_items = _int_val(compliance_stats, "compare_items")
    if not compare_items:
        compare_items = _int_val(diagnosis.get("section_pipeline") or {}, "compare_items")
    policies_discovered = _int_val(discovery, "policies_discovered")
    if not policies_discovered:
        policies_discovered = _int_val(ipc_summary, "policies_discovered")
    discovery_scope_mode = discovery.get("discovery_scope_mode") or compliance_stats.get(
        "discovery_scope_mode"
    )
    planner_calls = actuals.get("routing_planner_calls") or 0
    if (
        planner_calls > 0
        and policies_discovered > 12
        and discovery_scope_mode != "request"
    ):
        health_flags.append("routing_on_oversized_catalog")
    if (
        compare_items >= 15
        and policies_discovered > 12
        and discovery_scope_mode != "request"
        and "section_nc_regression" in health_flags
    ):
        health_flags.append("section_compare_wrong_universe")

    extract_quality = dict(
        (diagnosis.get("obligation_pipeline") or {}).get("extract_quality") or {}
    )
    batch_failures = _int_val(extract_quality, "batch_failures")
    llm_extract_rate = _float_val(extract_quality, "llm_extract_rate")
    if batch_failures >= 2 and llm_extract_rate is not None and llm_extract_rate < 0.85:
        health_flags.append("extract_structure_degraded")

    baseline_wall_ms = _int_val(baseline_metrics, "review_wall_ms")
    review_wall_ms = actuals.get("review_wall_ms") or 0
    max_speed_ratio = _float_val(baseline, "max_wall_speed_ratio") or 0.35
    if (
        baseline_wall_ms
        and review_wall_ms
        and review_wall_ms < int(baseline_wall_ms * (max_speed_ratio or 0.35))
        and baseline_min
        and violations_nc < baseline_min
        and "pathological_ipc_funnel" not in health_flags
    ):
        health_flags.append("review_wall_time_suspicious")

    min_compare_queued = _int_val(baseline_metrics, "compare_queued")
    if (
        compare_items >= 15
        and min_compare_queued
        and queued_actual < int(min_compare_queued * 0.5)
        and baseline_min
        and violations_nc < baseline_min
    ):
        health_flags.append("funnel_work_skipped")

    wall_min = (review_wall_ms / 60_000.0) if review_wall_ms else None

    pathological_ipc = "pathological_ipc_funnel" in health_flags
    ipc_interp_status = _ipc_interpretation_status(
        pathological=pathological_ipc,
        violations_nc=violations_nc,
        baseline_min=baseline_min,
        queued_actual=queued_actual,
        queued_baseline=queued_baseline,
        batches_actual=batches_actual,
        batches_baseline=batches_baseline,
        ipc_actual=ipc_actual,
    )
    if ipc_interp_status == "evidence_healthy":
        health_flags.append("ipc_evidence_healthy")
    elif ipc_interp_status == "aspirational_progress":
        health_flags.append("ipc_aspirational_band")

    deltas = {
        "obligation_ipc_rate": _metric_delta(
            actual=ipc_actual,
            baseline=ipc_baseline,
            status=ipc_status,
            delta=ipc_delta,
        ),
        "llm_rate_limit_events": _metric_delta(
            actual=rate_actual,
            baseline=rate_baseline,
            status=rate_status,
        ),
        "obligation_compare_llm_batches": _metric_delta(
            actual=batches_actual,
            baseline=batches_baseline,
            status=batches_status,
        ),
        "compare_queued": _metric_delta(
            actual=queued_actual,
            baseline=queued_baseline,
            status="ok" if not queued_baseline or queued_actual >= int(queued_baseline * 0.82) else "low",
        ),
    }

    return {
        "baseline_id": baseline_id,
        "funnel_story": _build_funnel_story(
            actuals,
            compare_items=compare_items or None,
            wall_min=wall_min,
            ipc_rate=ipc_actual,
        ),
        "deltas": deltas,
        "health_flags": sorted(set(health_flags)),
        "ipc_interpretation": {
            "hierarchy": ["violations_nc", "funnel_work", "ipc_rate_band"],
            "target_band_post_ipc2": [0.50, 0.65],
            "current_baseline_ipc": ipc_baseline,
            "status": ipc_interp_status,
        },
        "primary_accuracy": {
            "violations_nc": violations_nc,
            "baseline_min": baseline_min,
            "status": accuracy_status,
        },
        "actuals": {k: v for k, v in actuals.items() if k != "skip_by_reason"},
    }
