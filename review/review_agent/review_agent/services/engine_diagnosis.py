"""Assemble canonical engine_diagnosis block from pipeline state (Phase P5)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from document_core.schemas.compliance import ComplianceFinding
from review_agent.config import get_settings
from review_agent.services.accuracy_paths import build_accuracy_paths_summary
from review_agent.services.baseline_interpretation import (
    build_baseline_interpretation,
    load_baseline_profile,
)
from review_agent.services.config_advisory import build_config_pressure_diagnosis
from review_agent.state.review_state import ReviewState

ENGINE_DIAGNOSIS_VERSION = "1.0"


def _int_val(data: dict[str, Any], key: str, default: int = 0) -> int:
    raw = data.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _merge_skip_by_reason(*sources: dict[str, Any] | None) -> dict[str, int]:
    merged: dict[str, int] = {}
    for source in sources:
        if not source:
            continue
        for key, value in source.items():
            try:
                merged[str(key)] = merged.get(str(key), 0) + int(value)
            except (TypeError, ValueError):
                continue
    return merged


def _zero_hit_section_ids(state: ReviewState) -> list[str]:
    ids: list[str] = []
    for entry in state.get("failed_sections") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("error_code") == "retrieval_zero_hit":
            sid = str(entry.get("section_id") or "").strip()
            if sid:
                ids.append(sid)
    return sorted(set(ids))


def _detect_pipeline_mode(
    *,
    funnel: dict[str, Any] | None,
    compliance_stats: dict[str, Any],
    section_compare_ran: bool,
) -> str:
    if funnel:
        if section_compare_ran or _int_val(compliance_stats, "sections_total") > 0:
            return "hybrid"
        return "obligation_routing"
    mode = str(compliance_stats.get("compliance_mode") or "")
    if mode == "obligation_routing":
        return "obligation_routing"
    return "section_first"


def _section_ipc_pct(review_confidence: dict[str, Any]) -> float:
    raw = review_confidence.get("ipc_section_pct")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    return 0.0


def _canonical_skip_by_reason(
    evidence_skip: dict[str, Any],
    funnel_dict: dict[str, Any] | None,
) -> dict[str, int]:
    funnel_skip = dict((funnel_dict or {}).get("skip_by_reason") or {})
    if evidence_skip:
        return {str(k): int(v) for k, v in evidence_skip.items() if v is not None}
    if funnel_skip:
        return {str(k): int(v) for k, v in funnel_skip.items() if v is not None}
    return _merge_skip_by_reason(evidence_skip, funnel_skip)


def _planner_fallback_ipc_count(state: ReviewState) -> int:
    routing = state.get("obligation_routing_by_id") or {}
    evidence = state.get("obligation_evidence_by_id") or {}
    count = 0
    for ob_id, plan_raw in routing.items():
        plan = plan_raw if isinstance(plan_raw, dict) else {}
        if plan.get("routing_source") != "planner_fallback":
            continue
        ev = evidence.get(ob_id) or {}
        decision = ev.get("decision") if isinstance(ev, dict) else getattr(ev, "decision", None)
        if decision == "ipc":
            count += 1
    return count


def build_engine_diagnosis(
    *,
    state: ReviewState,
    findings: list[ComplianceFinding],
    compliance_stats: dict[str, Any],
    final_verify_stats: dict[str, Any],
    gap_status_summary: dict[str, Any],
    review_confidence: dict[str, Any],
) -> dict[str, Any]:
    """Pure assembler — references existing counters; does not recompute pipeline logic."""
    funnel = compliance_stats.get("obligation_pipeline_funnel")
    funnel_dict = dict(funnel) if isinstance(funnel, dict) else None
    routing_summary = dict(compliance_stats.get("routing_summary") or {})
    evidence_skip = dict(compliance_stats.get("obligation_evidence_skip_by_reason") or {})
    section_coverage = dict(state.get("section_coverage") or {})
    zero_hit_ids = _zero_hit_section_ids(state)
    section_compare_ran = bool(state.get("section_compare_items"))

    pipeline_mode = _detect_pipeline_mode(
        funnel=funnel_dict,
        compliance_stats=compliance_stats,
        section_compare_ran=section_compare_ran,
    )

    obligation_ipc = _int_val(compliance_stats, "obligation_ipc_findings")
    obligation_compare = _int_val(compliance_stats, "obligation_compare_count")
    obligation_ipc_rate = routing_summary.get("ipc_rate")
    if obligation_ipc_rate is None and obligation_ipc + obligation_compare > 0:
        routed = max(obligation_ipc + obligation_compare, 1)
        obligation_ipc_rate = round(obligation_ipc / routed, 3)

    runtime = dict(compliance_stats.get("runtime_settings") or {})
    ipc_skip = _canonical_skip_by_reason(evidence_skip, funnel_dict)

    ipc_summary: dict[str, Any] = {
        "obligation_ipc_findings": obligation_ipc,
        "obligation_compare_count": obligation_compare,
        "obligation_ipc_rate": obligation_ipc_rate,
        "section_ipc_pct": _section_ipc_pct(review_confidence),
        "skip_by_reason": ipc_skip,
        "policies_discovered": _int_val(compliance_stats, "discovery_returned"),
        "discovery_scope_mode": compliance_stats.get("discovery_scope_mode"),
        "cutover_mode": runtime.get(
            "obligation_section_cutover_mode",
            compliance_stats.get("obligation_section_cutover_mode", "ipc_fallback"),
        ),
        "planner_fallback_ipc_count": _planner_fallback_ipc_count(state),
    }

    obligation_pipeline: dict[str, Any] | None = None
    if funnel_dict or routing_summary or evidence_skip:
        obligation_pipeline = {}
        if funnel_dict:
            obligation_pipeline["funnel"] = funnel_dict
        if routing_summary:
            obligation_pipeline["routing_summary"] = routing_summary
        if evidence_skip:
            obligation_pipeline["evidence_skip_by_reason"] = evidence_skip
        cap_dropped = _int_val(compliance_stats, "obligation_cap_dropped_count")
        cap_section_ids = compliance_stats.get("obligation_cap_dropped_section_ids")
        cap_mode = compliance_stats.get("obligation_cap_mode")
        obligation_count = _int_val(compliance_stats, "obligation_count")
        if obligation_count or cap_mode is not None or cap_dropped or cap_section_ids:
            obligation_pipeline["extract_cap"] = {
                "dropped_count": cap_dropped,
                "dropped_section_ids": list(cap_section_ids or []),
                "cap_mode": cap_mode,
                "post_cap_count": obligation_count,
            }
        extract_batch_failures = _int_val(compliance_stats, "extract_batch_failures")
        extract_single_recovered = _int_val(compliance_stats, "extract_single_recovered")
        extract_fallback_count = _int_val(compliance_stats, "extract_fallback_count")
        extract_llm_count = _int_val(compliance_stats, "extract_llm_count")
        if (
            extract_batch_failures
            or extract_single_recovered
            or extract_fallback_count
            or extract_llm_count
        ):
            total_extracted = extract_llm_count + extract_fallback_count
            llm_rate = (
                round(extract_llm_count / total_extracted, 3)
                if total_extracted
                else None
            )
            obligation_pipeline["extract_quality"] = {
                "batch_failures": extract_batch_failures,
                "single_recovered": extract_single_recovered,
                "fallback_count": extract_fallback_count,
                "llm_extract_rate": llm_rate,
            }
        retrieval_efficiency = {
            "mcp_calls": _int_val(compliance_stats, "obligation_retrieval_mcp_calls"),
            "ladder_early_exit_count": _int_val(
                compliance_stats, "obligation_retrieval_ladder_early_exit_count"
            ),
            "queries_executed_total": _int_val(
                compliance_stats, "obligation_retrieval_queries_executed_total"
            ),
            "queries_planned_total": _int_val(
                compliance_stats, "obligation_retrieval_queries_planned_total"
            ),
        }
        if any(retrieval_efficiency.values()):
            obligation_pipeline["retrieval_efficiency"] = retrieval_efficiency
        section_skip = _int_val(compliance_stats, "obligation_retrieval_section_skip_count")
        hit_reuse = _int_val(compliance_stats, "obligation_section_hit_reuse_count")
        if section_skip or hit_reuse:
            obligation_pipeline["section_path_optimization"] = {
                "section_skip_count": section_skip,
                "section_hit_reuse_count": hit_reuse,
            }

    sections_reviewable = _int_val(
        section_coverage,
        "reviewable_count",
        _int_val(compliance_stats, "sections_total"),
    )
    sections_compared = _int_val(
        compliance_stats,
        "compare_items",
        _int_val(compliance_stats, "sections_with_policy"),
    )

    section_pipeline: dict[str, Any] = {
        "sections_reviewable": sections_reviewable,
        "sections_compared": sections_compared,
        "compare_items": sections_compared,
        "retrieval_zero_hit_sections": _int_val(
            compliance_stats, "retrieval_zero_hit_sections", len(zero_hit_ids)
        ),
        "retrieval_zero_hit_section_ids": zero_hit_ids,
        "compare_hit_selection": dict(compliance_stats.get("compare_hit_selection") or {}),
        "compare_selection_empty_ipc_count": _int_val(
            compliance_stats, "compare_selection_empty_ipc_count"
        ),
        "coverage_gate_ipc_count": _int_val(compliance_stats, "coverage_gate_ipc_count"),
    }

    recovery_gap = dict(gap_status_summary)
    compare_omitted = _int_val(final_verify_stats, "compare_omitted_recovered")
    if compare_omitted:
        recovery_gap["compare_omitted_recovered"] = compare_omitted
    gap_sections = _int_val(final_verify_stats, "gap_sections")
    if gap_sections:
        recovery_gap["gap_sections"] = gap_sections
    promoted_eligible = _int_val(compliance_stats, "recovery_compare_omitted_eligible")
    if promoted_eligible:
        recovery_gap["compare_omitted_eligible"] = promoted_eligible

    resilience: dict[str, Any] = {
        "llm_rate_limit_events": _int_val(compliance_stats, "llm_rate_limit_events"),
        "llm_batches_failed": _int_val(compliance_stats, "llm_batches_failed"),
        "llm_review_posture": compliance_stats.get("llm_review_posture"),
        "llm_hot_structure_splits_used": _int_val(
            compliance_stats, "llm_hot_structure_splits_used"
        ),
        "obligation_compare_batches_failed": _int_val(
            funnel_dict or {}, "llm_batches_failed"
        ),
        "breaker_open_events": _int_val(compliance_stats, "breaker_open_events"),
        "breaker_open_events_llm": _int_val(compliance_stats, "breaker_open_events_llm"),
        "breaker_open_events_mcp": _int_val(compliance_stats, "breaker_open_events_mcp"),
    }

    mcp_hits = _int_val(compliance_stats, "mcp_cache_hits")
    mcp_misses = _int_val(compliance_stats, "mcp_cache_misses")
    infrastructure: dict[str, Any] = {}
    if mcp_hits or mcp_misses:
        hit_rate = compliance_stats.get("mcp_cache_hit_rate")
        if hit_rate is None and (mcp_hits + mcp_misses):
            hit_rate = round(mcp_hits / (mcp_hits + mcp_misses), 3)
        infrastructure["mcp_cache"] = {
            "hits": mcp_hits,
            "misses": mcp_misses,
            "hit_rate": hit_rate,
        }
    llm_batches = _int_val(compliance_stats, "llm_batches_actual")
    if llm_batches:
        config_max = _int_val(compliance_stats, "llm_batches_config_max")
        token_limited = _int_val(compliance_stats, "llm_batches_token_limited")
        infrastructure["section_compare_batches"] = {
            "actual": llm_batches,
            "failed": _int_val(compliance_stats, "llm_batches_failed"),
            "config_max": config_max,
            "token_limited": token_limited,
            "est_tokens_max_batch": _int_val(compliance_stats, "compare_est_tokens_max_batch"),
            "pack_mode": compliance_stats.get("compare_token_pack_mode"),
            "budget_mode": compliance_stats.get("compare_token_budget_mode"),
        }

    cfg = get_settings()
    tenant_id = str(state.get("tenant_id") or "")
    config_pressure = build_config_pressure_diagnosis(
        settings=cfg,
        tenant_id=tenant_id,
        compliance_stats=compliance_stats,
        reviewable_sections=sections_reviewable,
    )
    infrastructure["config_pressure"] = config_pressure

    quote_skipped = _int_val(compliance_stats, "quote_repair_quota_skipped")
    quote_batch_calls = _int_val(compliance_stats, "quote_repair_batch_calls")
    if quote_skipped or quote_batch_calls or compliance_stats.get("grounding_fail_open"):
        grounding_diag: dict[str, Any] = {
            "quote_repair_batch_calls": quote_batch_calls,
            "quote_repair_quota_skipped": quote_skipped,
            "grounding_fail_open": bool(compliance_stats.get("grounding_fail_open")),
        }
        fail_reason = str(compliance_stats.get("grounding_fail_reason") or "").strip()
        if fail_reason:
            grounding_diag["grounding_fail_reason"] = fail_reason[:200]
        infrastructure["grounding"] = grounding_diag

    accuracy_paths = build_accuracy_paths_summary(
        compliance_stats,
        final_verify_stats,
        reviewable_sections=sections_reviewable,
        settings=cfg,
    )

    diagnosis: dict[str, Any] = {
        "schema_version": ENGINE_DIAGNOSIS_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pipeline_mode": pipeline_mode,
        "pipeline_topology": {
            "review_pipeline_mode": str(compliance_stats.get("review_pipeline_topology") or compliance_stats.get("review_pipeline_wiring") or ""),
            "pipeline_join": str(compliance_stats.get("pipeline_join") or ""),
        },
        "ipc_summary": ipc_summary,
        "section_pipeline": section_pipeline,
        "recovery": {
            "final_verify": dict(final_verify_stats),
            "gap_status_summary": recovery_gap,
        },
        "resilience": resilience,
        "review_confidence": dict(review_confidence),
        "accuracy_paths": accuracy_paths,
    }
    if obligation_pipeline:
        diagnosis["obligation_pipeline"] = obligation_pipeline
    if infrastructure:
        diagnosis["infrastructure"] = infrastructure

    policies_discovered = _int_val(compliance_stats, "discovery_returned")
    discovery_scope_mode = compliance_stats.get("discovery_scope_mode")
    if policies_discovered or discovery_scope_mode:
        diagnosis["discovery"] = {
            "policies_discovered": policies_discovered,
            "discovery_scope_mode": discovery_scope_mode,
        }

    profile_id = str(cfg.baseline_profile or "").strip()
    if profile_id:
        baseline = load_baseline_profile(profile_id)
        if baseline:
            diagnosis["baseline_interpretation"] = build_baseline_interpretation(
                diagnosis,
                compliance_stats,
                baseline=baseline,
            )

    return diagnosis
