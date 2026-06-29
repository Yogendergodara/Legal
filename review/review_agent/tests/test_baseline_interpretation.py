"""Tests for Phase G baseline interpretation."""

from __future__ import annotations

from review_agent.services.baseline_interpretation import (
    build_baseline_interpretation,
    has_accuracy_regression,
    load_baseline_profile,
)


def _atlassian_baseline() -> dict:
    baseline = load_baseline_profile("atlassian_v1")
    assert baseline is not None
    return baseline


def _diagnosis_and_stats(
    *,
    violations_nc: int = 6,
    ipc_rate: float = 0.9,
    rate_limit_events: int = 51,
    compare_queued: int = 34,
    llm_batches: int = 8,
) -> tuple[dict, dict]:
    diagnosis = {
        "ipc_summary": {
            "obligation_ipc_rate": ipc_rate,
            "obligation_ipc_findings": 72,
            "obligation_compare_count": 8,
            "skip_by_reason": {"routing_or_skip": 41},
        },
        "resilience": {"llm_rate_limit_events": rate_limit_events},
        "obligation_pipeline": {
            "funnel": {
                "extracted": 80,
                "compare_queued": compare_queued,
                "llm_batches": llm_batches,
            },
            "routing_summary": {"obligation_count": 80, "planner_calls": 0},
        },
    }
    stats = {
        "non_compliant_count": violations_nc,
        "obligation_compare_llm_batches": llm_batches,
    }
    return diagnosis, stats


def test_load_atlassian_snapshot():
    baseline = _atlassian_baseline()
    assert baseline["baseline_id"] == "atlassian_v1"
    assert baseline["metrics"]["compare_queued"] == 34
    assert baseline["min_violations"] == 6


def test_baseline_interpretation_matches_snapshot():
    baseline = _atlassian_baseline()
    diagnosis, stats = _diagnosis_and_stats()
    interp = build_baseline_interpretation(diagnosis, stats, baseline=baseline)

    assert interp["baseline_id"] == "atlassian_v1"
    assert "80 extracted" in interp["funnel_story"]
    assert "ipc=0.9" in interp["funnel_story"]
    assert interp["ipc_interpretation"]["status"] == "evidence_healthy"
    assert "ipc_evidence_healthy" in interp["health_flags"]
    assert interp["deltas"]["obligation_ipc_rate"]["status"] == "ok"
    assert interp["deltas"]["llm_rate_limit_events"]["status"] == "ok"
    assert interp["primary_accuracy"]["status"] == "ok"
    assert "ipc_expected_high" in interp["health_flags"]


def test_rate_limit_improved_vs_baseline():
    baseline = _atlassian_baseline()
    diagnosis, stats = _diagnosis_and_stats(rate_limit_events=12)
    interp = build_baseline_interpretation(diagnosis, stats, baseline=baseline)

    assert interp["deltas"]["llm_rate_limit_events"]["status"] == "improved"
    assert "rate_limit_elevated" not in interp["health_flags"]


def test_rate_limit_elevated_flag():
    baseline = _atlassian_baseline()
    diagnosis, stats = _diagnosis_and_stats(rate_limit_events=70)
    interp = build_baseline_interpretation(diagnosis, stats, baseline=baseline)

    assert interp["deltas"]["llm_rate_limit_events"]["status"] == "elevated"
    assert "rate_limit_elevated" in interp["health_flags"]


def test_accuracy_regression_fails():
    baseline = _atlassian_baseline()
    diagnosis, stats = _diagnosis_and_stats(violations_nc=4)
    interp = build_baseline_interpretation(diagnosis, stats, baseline=baseline)

    assert interp["primary_accuracy"]["status"] == "regression"
    assert "accuracy_regression" in interp["health_flags"]
    assert has_accuracy_regression(interp) is True


def test_ipc_drift_review_info_only():
    baseline = _atlassian_baseline()
    diagnosis, stats = _diagnosis_and_stats(ipc_rate=0.92, violations_nc=6)
    interp = build_baseline_interpretation(diagnosis, stats, baseline=baseline)

    assert "ipc_drift_review" not in interp["health_flags"]
    assert has_accuracy_regression(interp) is False

    low_ipc_baseline = {
        **baseline,
        "metrics": {**baseline["metrics"], "obligation_ipc_rate": 0.55},
    }
    diagnosis2, stats2 = _diagnosis_and_stats(ipc_rate=0.66, violations_nc=6)
    interp2 = build_baseline_interpretation(diagnosis2, stats2, baseline=low_ipc_baseline)
    assert "ipc_drift_review" in interp2["health_flags"]


def test_compare_funnel_stuck():
    baseline = _atlassian_baseline()
    diagnosis, stats = _diagnosis_and_stats(compare_queued=40, llm_batches=8)
    interp = build_baseline_interpretation(diagnosis, stats, baseline=baseline)
    assert "compare_funnel_stuck" in interp["health_flags"]


def test_pathological_ipc_funnel_flag():
    baseline = _atlassian_baseline()
    diagnosis, stats = _diagnosis_and_stats(compare_queued=0, ipc_rate=1.0)
    interp = build_baseline_interpretation(diagnosis, stats, baseline=baseline)
    assert "pathological_ipc_funnel" in interp["health_flags"]


def test_section_nc_regression_flag():
    baseline = _atlassian_baseline()
    diagnosis, stats = _diagnosis_and_stats(violations_nc=2, ipc_rate=0.92, compare_queued=10)
    interp = build_baseline_interpretation(diagnosis, stats, baseline=baseline)
    assert "section_nc_regression" in interp["health_flags"]
    assert "accuracy_regression" in interp["health_flags"]


def test_f5_recovery_starved_flag():
    baseline = _atlassian_baseline()
    diagnosis, stats = _diagnosis_and_stats(violations_nc=6)
    diagnosis["accuracy_paths"] = {"recovery_starved": True}
    interp = build_baseline_interpretation(diagnosis, stats, baseline=baseline)
    assert "f5_recovery_starved" in interp["health_flags"]


def test_routing_on_oversized_catalog_flag():
    baseline = _atlassian_baseline()
    diagnosis, stats = _diagnosis_and_stats(violations_nc=6)
    diagnosis["discovery"] = {"policies_discovered": 26, "discovery_scope_mode": "indexed"}
    diagnosis["obligation_pipeline"]["routing_summary"]["planner_calls"] = 4
    stats["routing_planner_calls"] = 4
    interp = build_baseline_interpretation(diagnosis, stats, baseline=baseline)
    assert "routing_on_oversized_catalog" in interp["health_flags"]


def test_section_compare_wrong_universe_flag():
    baseline = _atlassian_baseline()
    diagnosis, stats = _diagnosis_and_stats(violations_nc=2, ipc_rate=0.92, compare_queued=10)
    diagnosis["discovery"] = {"policies_discovered": 26, "discovery_scope_mode": "indexed"}
    diagnosis["section_pipeline"] = {"compare_items": 28}
    interp = build_baseline_interpretation(diagnosis, stats, baseline=baseline)
    assert "section_nc_regression" in interp["health_flags"]
    assert "section_compare_wrong_universe" in interp["health_flags"]


def test_extract_structure_degraded_flag():
    baseline = _atlassian_baseline()
    diagnosis, stats = _diagnosis_and_stats(violations_nc=6)
    diagnosis["obligation_pipeline"]["extract_quality"] = {
        "batch_failures": 3,
        "llm_extract_rate": 0.7,
    }
    interp = build_baseline_interpretation(diagnosis, stats, baseline=baseline)
    assert "extract_structure_degraded" in interp["health_flags"]


def test_review_wall_time_suspicious_flag():
    baseline = _atlassian_baseline()
    diagnosis, stats = _diagnosis_and_stats(violations_nc=1)
    stats["review_wall_ms"] = 278_000
    interp = build_baseline_interpretation(diagnosis, stats, baseline=baseline)
    assert "review_wall_time_suspicious" in interp["health_flags"]
    assert "wall=" in interp["funnel_story"]


def test_ipc_evidence_healthy_high_ipc_with_funnel_work():
    baseline = _atlassian_baseline()
    diagnosis, stats = _diagnosis_and_stats(violations_nc=6, ipc_rate=0.9, compare_queued=34, llm_batches=8)
    interp = build_baseline_interpretation(diagnosis, stats, baseline=baseline)
    assert interp["ipc_interpretation"]["status"] == "evidence_healthy"
    assert "ipc_evidence_healthy" in interp["health_flags"]


def test_ipc_aspirational_band_post_ipc2():
    baseline = _atlassian_baseline()
    diagnosis, stats = _diagnosis_and_stats(violations_nc=6, ipc_rate=0.58, compare_queued=34, llm_batches=8)
    interp = build_baseline_interpretation(diagnosis, stats, baseline=baseline)
    assert interp["ipc_interpretation"]["status"] == "aspirational_progress"
    assert "ipc_aspirational_band" in interp["health_flags"]


def test_ipc_pathological_status():
    baseline = _atlassian_baseline()
    diagnosis, stats = _diagnosis_and_stats(compare_queued=0, ipc_rate=1.0)
    interp = build_baseline_interpretation(diagnosis, stats, baseline=baseline)
    assert interp["ipc_interpretation"]["status"] == "pathological"
