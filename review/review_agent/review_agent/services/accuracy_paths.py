"""Accuracy path ledger — intentional LLM save (F1–F4) and recovery (F5) observability."""

from __future__ import annotations

from typing import Any


def _int_val(data: dict[str, Any], key: str, default: int = 0) -> int:
    raw = data.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _top_skip_reasons(skip_by_reason: dict[str, Any], limit: int = 3) -> dict[str, int]:
    ranked = sorted(
        ((str(k), _int_val({"v": v}, "v")) for k, v in skip_by_reason.items()),
        key=lambda pair: pair[1],
        reverse=True,
    )
    return {k: v for k, v in ranked[:limit] if v > 0}


def build_accuracy_paths_summary(
    compliance_stats: dict[str, Any],
    final_verify_stats: dict[str, Any],
    *,
    reviewable_sections: int,
    settings: Any | None = None,
) -> dict[str, Any]:
    """Assemble save/recover counters from existing pipeline stats (no graph recompute)."""
    funnel = compliance_stats.get("obligation_pipeline_funnel") or {}
    funnel_dict = dict(funnel) if isinstance(funnel, dict) else {}
    routing_summary = dict(compliance_stats.get("routing_summary") or {})
    evidence_skip = dict(compliance_stats.get("obligation_evidence_skip_by_reason") or {})

    alias_hits = _int_val(compliance_stats, "obligation_alias_hit_count")
    compare_pre_ipc = _int_val(funnel_dict, "compare_pre_ipc")
    if not compare_pre_ipc:
        extracted = _int_val(funnel_dict, "extracted")
        compare_queued = _int_val(funnel_dict, "compare_queued")
        if extracted:
            compare_pre_ipc = max(extracted - compare_queued, 0)

    coverage_gate_ipc = _int_val(compliance_stats, "coverage_gate_ipc_count")
    classify_lexical = _int_val(compliance_stats, "classify_lexical_skipped")
    classify_llm = _int_val(compliance_stats, "classify_llm_sections")

    planner_calls = routing_summary.get("planner_calls")
    if planner_calls is None:
        planner_calls = _int_val(compliance_stats, "routing_planner_calls")
    try:
        planner_calls_int = int(planner_calls or 0)
    except (TypeError, ValueError):
        planner_calls_int = 0

    unclear_recompared = _int_val(final_verify_stats, "unclear_recompared")
    gap_batches = _int_val(final_verify_stats, "gap_recompare_batches")
    conflict_batches = _int_val(final_verify_stats, "conflict_recompare_batches")
    compare_omitted = _int_val(final_verify_stats, "compare_omitted_recovered")
    compare_omitted_eligible = _int_val(compliance_stats, "recovery_compare_omitted_eligible")
    gap_sections = _int_val(final_verify_stats, "gap_sections")

    coverage_gate_recompare_enabled = True
    if settings is not None:
        coverage_gate_recompare_enabled = bool(
            getattr(settings, "final_verify_coverage_gate_recompare_enabled", True)
        )

    save_block: dict[str, Any] = {
        "classify_lexical_skipped": classify_lexical,
        "classify_llm_sections": classify_llm,
        "coverage_gate_ipc_sections": coverage_gate_ipc,
        "compare_sections_skipped_pre_compare": coverage_gate_ipc,
        "obligation_alias_hits": alias_hits,
        "planner_calls_actual": planner_calls_int,
        "planner_calls_avoided_estimate": alias_hits,
        "obligation_evidence_ipc": compare_pre_ipc,
        "evidence_skip_top_reasons": _top_skip_reasons(evidence_skip),
    }

    recover_block: dict[str, Any] = {
        "unclear_recompared": unclear_recompared,
        "gap_recompare_batches": gap_batches,
        "conflict_recompare_batches": conflict_batches,
        "compare_omitted_recovered": compare_omitted,
        "compare_omitted_eligible": compare_omitted_eligible,
        "gap_sections": gap_sections,
        "coverage_gate_recompare_candidates": _int_val(
            final_verify_stats, "coverage_gate_recompare_candidates"
        ),
        "coverage_gate_recompare_attempted": _int_val(
            final_verify_stats, "coverage_gate_recompare_attempted"
        ),
        "coverage_gate_recompare_resolved": _int_val(
            final_verify_stats, "coverage_gate_recompare_resolved"
        ),
        "coverage_gate_recompare_eligible": coverage_gate_recompare_enabled,
    }

    recovery_compare = unclear_recompared + compare_omitted
    recovery_starved = (
        gap_sections >= 5 and compare_omitted == 0 and compare_omitted_eligible > 0
    )
    net_story = (
        f"saved_classify≈{classify_lexical}; "
        f"saved_ob_compare≈{compare_pre_ipc}; "
        f"saved_planner≈{alias_hits}; "
        f"coverage_gate_ipc≈{coverage_gate_ipc}; "
        f"recovery_compare≈{recovery_compare}"
    )

    evidence_gate_healthy = True
    if settings is not None:
        min_score = float(getattr(settings, "evidence_min_score", 0.35))
        min_overlap = float(getattr(settings, "evidence_min_concept_overlap", 0.25))
        if min_score < 0.25 or min_overlap < 0.15:
            evidence_gate_healthy = False

    return {
        "save": save_block,
        "recover": recover_block,
        "reviewable_sections": reviewable_sections,
        "evidence_gate_healthy": evidence_gate_healthy,
        "recovery_starved": recovery_starved,
        "net_story": net_story,
    }
