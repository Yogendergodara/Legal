"""Review agent runtime configuration — section-first production pipeline."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

logger = logging.getLogger(__name__)
_config_cap_warned = False


class ReviewSettings(BaseSettings):
    """Settings for section-first compliance review."""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    compliance_llm_temperature: float = 0.0
    # contract_routing.py only — pipeline retries live in llm_gateway.invoke_structured
    compliance_llm_max_retries: int = 1
    compliance_llm_role: str = "reasoning"
    compliance_llm_max_tokens: int = 2048

    llm_global_concurrency: int = 2
    llm_rate_limit_max_retries: int = 3
    llm_rate_limit_backoff_base_seconds: float = 2.0
    llm_rate_limit_backoff_max_seconds: float = 30.0
    llm_rate_limit_profile: Literal["default", "mistral_conservative"] = "default"
    llm_review_posture_enabled: bool = True
    # Phase B-RC — review-scoped quota counters and HOT pacing (no batch size change)
    llm_review_scope_reset_events: bool = True
    llm_hot_structure_split_max: int = 12
    llm_hot_acquire_pause_enabled: bool = True
    llm_hot_acquire_pause_max_seconds: float = 1.5
    # TEMP Phase B — remove with review_agent/models/llm_key_pool.py
    llm_key_pool_enabled: bool = False
    llm_api_keys: str = ""

    review_min_section_chars: int = 40

    review_policy_scope: Literal["indexed", "request"] = "indexed"
    contract_routing_mode: Literal["llm", "lexical"] = "llm"
    contract_routing_max_chars: int = 12_000
    review_plan_llm_max_tokens: int = 1024
    # 0 = no flat cap after grouping; group cap only (discovery_max_policy_groups*)
    discovery_max_policies: int = 0
    discovery_group_mode: Literal["category", "flat"] = "category"
    discovery_group_cap_mode: Literal["fixed", "adaptive"] = "adaptive"
    discovery_max_policy_groups: int = 6
    discovery_min_policy_groups: int = 6
    discovery_max_policy_groups_ceiling: int = 20
    discovery_topic_cap_mode: Literal["fixed", "adaptive"] = "adaptive"
    discovery_max_topics: int = 8
    discovery_max_topics_ceiling: int = 20
    discovery_top_k_per_topic: int = 5
    discovery_min_score: float = 0.08
    discovery_contract_type_filter: bool = True
    discovery_contract_type_fallback_min_hits: int = 4
    discovery_section_category_sweep: bool = True
    discovery_category_reserve_slots: bool = True
    discovery_category_score_boost: float = 0.15
    discovery_vendor_complete_threshold: int = 10

    section_classify_batch_size: int = 4
    section_classify_batch_size_on_parse_fail: int = 2
    section_classify_max_chars: int = 12_000
    section_classify_mode: Literal["lexical_first", "llm_only"] = "lexical_first"
    section_classify_batch_retry_single: bool = True
    section_lexical_body_scan_chars: int = 800
    section_lexical_full_body_max_chars: int = 4000
    section_classify_block_general_substantive: bool = True
    section_cross_ref_enabled: bool = True
    section_compare_context_max_chars: int = 3000
    gap_boilerplate_skip_compare: bool = True
    retrieval_recall_top_k: int = 30
    retrieval_final_top_k: int = 12
    retrieval_max_attempts: int = 3
    retrieval_broaden_on_retry: bool = True
    retrieval_category_hard_filter: bool = True
    # SR-01 — meaning-first recall inside scoped policies (tag-second precision)
    retrieval_meaning_first_enabled: bool = False
    retrieval_section_query_max_chars: int = 2000
    retrieval_category_filter_fallback: bool = False
    retrieval_scope_fallback_on_category_miss: bool = True
    retrieval_penalize_preamble_general: bool = True
    retrieval_skip_hard_filter_for_general: bool = True
    retrieval_max_hits_per_document: int = 3
    named_policy_routing_enabled: bool = True
    retrieval_relevance_gate_enabled: bool = True
    retrieval_relevance_min_score: float = 0.2
    retrieval_relevance_keep_best_fallback: bool = False
    policy_coverage_enabled: bool = True
    policy_coverage_min_score: float = 0.34
    policy_coverage_require_specific_overlap: bool = True
    retrieval_coverage_filter_aligned: bool = True
    incorporation_guard_enabled: bool = True
    topic_mismatch_guard_enabled: bool = True
    equivalence_guard_enabled: bool = True
    finding_dedupe_topic_cluster: bool = True
    retrieval_category_min_overlap: int = 0
    discovery_warn_on_cap: bool = True
    section_compare_batch_size: int = 8
    section_compare_max_findings_per_section: int = 4
    finding_dedupe_across_policies: bool = True
    section_compare_max_tokens: int = 48_000
    section_compare_max_section_chars: int = 32_000
    section_retrieval_concurrency: int = 8
    section_compare_concurrency: int = 2
    compare_policy_hit_mode: Literal["all_top_k", "category_aligned", "primary_only"] = "category_aligned"
    compare_max_policy_hits: int = 3
    compare_hit_min_relevance_score: float = 0.35
    compare_hit_allow_primary_fallback: bool = False
    compare_hit_trust_retrieval_gate: bool = True
    compare_batch_retry_single: bool = True
    compare_failure_transient_inconclusive: bool = True
    compare_quote_anchor_enabled: bool = True
    compare_token_budget_mode: Literal["aligned", "legacy"] = "aligned"
    compare_token_pack_mode: Literal["first_fit", "best_fit"] = "best_fit"
    playbook_compare_max_chars: int = 2000

    final_gap_verify_enabled: bool = True
    final_gap_recall_top_k: int = 30
    final_verify_unclear_recompare_enabled: bool = True
    final_verify_unclear_recompare_max_sections: int = 8
    final_verify_unclear_recompare_cap_mode: Literal["fixed", "adaptive"] = "adaptive"
    final_verify_coverage_gate_recompare_enabled: bool = True

    config_advisory_enabled: bool = True

    gap_status_substantive_inconclusive: bool = True
    gap_upgrade_after_gap_llm: bool = True

    enforce_section_coverage: bool = True
    review_preflight_enabled: bool = True
    review_preflight_mcp_capability_probe: bool = True
    review_log_json: bool = False
    review_metrics_enabled: bool = False

    document_mcp_timeout_seconds: float = 60.0
    document_mcp_health_timeout_seconds: float = 5.0
    document_mcp_ingest_timeout_seconds: float = 120.0
    document_mcp_search_timeout_seconds: float = 30.0

    # PF-1D — MCP search cache + HTTP pool (Tier 4)
    mcp_search_cache_enabled: bool = True
    mcp_http_max_keepalive_connections: int = 40
    mcp_http_max_connections: int = 100
    # MCP global in-flight cap (mirrors llm_global_concurrency; process-wide singleton)
    mcp_global_concurrency: int = 6
    mcp_semaphore_acquire_timeout_seconds: float = 60.0
    mcp_semaphore_acquire_warn_seconds: float = 30.0

    playbook_enrich_compare: bool = True
    playbook_load_registry: bool = False
    grounding_downgrade_not_drop: bool = True
    grounding_rerun_coverage: bool = True
    grounding_relax_compliant_empty_policy: bool = True
    grounding_skip_compare_validated_quotes: bool = True
    conflict_emit_on_skip: bool = False

    engine_diagnosis_enabled: bool = True
    # Phase G — optional baseline profile for funnel interpretation (empty = off)
    baseline_profile: str = ""

    # PF-1C / PG-7 — serial default for production; parallel_hybrid for pilot tenants
    review_pipeline_mode: Literal["serial", "parallel_hybrid"] = "serial"
    review_pipeline_tenant_allowlist: str = ""
    obligation_retrieval_skip_resolved_sections: bool = True
    # OB-01 — when parallel_hybrid, do not skip obligations on pre-compare section hits alone
    obligation_skip_resolved_parallel_guard: bool = True
    obligation_retrieval_section_hit_reuse: bool = True

    compare_branch_fail_open: bool = True
    grounding_branch_fail_open: bool = True

    artifact_include_hit_refs: bool = True
    artifact_max_hit_refs_per_section: int = 10
    report_llm_summary: bool = False
    report_llm_summary_max_tokens: int = 256

    obligation_routing_enabled: bool = False
    obligation_extract_enabled: bool = True
    obligation_extract_batch_size: int = 6
    obligation_extract_batch_retry_single: bool = True
    obligation_extract_max_section_chars: int = 8000

    semantic_planner_enabled: bool = True
    semantic_planner_batch_size: int = 10
    semantic_planner_max_obligation_chars: int = 1500
    routing_alias_min_score: float = 0.92
    routing_alias_token_fallback_enabled: bool = True
    routing_compare_min_confidence: float = 0.75
    routing_ipc_max_confidence: float = 0.60
    # PR-01 / PR-06 — floor planner confidence when obligation cites a named policy
    routing_planner_explicit_mention_confidence_floor: float = 0.55

    catalog_match_top_k: int = 12
    catalog_match_max_candidates: int = 8
    catalog_match_min_score: float = 0.25
    catalog_match_obligation_fallback_enabled: bool = True
    catalog_match_title_min_score: float = 0.15
    catalog_match_max_queries: int = 4
    # PR-05B / IPC4 — run catalog search even when planner confidence < routing_ipc_max_confidence
    catalog_match_search_on_low_confidence: bool = True
    ipc3_catalog_marginal_compare_enabled: bool = False
    ipc3_catalog_marginal_min_score: float = 0.22
    routing_discovery_before_match: bool = True
    # IPC4 — deterministic catalog recovery when semantic search returns zero candidates
    catalog_match_taxonomy_recovery_enabled: bool = True
    catalog_match_taxonomy_recovery_min_score: float = 0.08
    catalog_match_taxonomy_recovery_max_candidates: int = 3
    catalog_match_broad_fence_min_confidence: float = 0.65
    catalog_match_broad_fence_min_score: float = 0.05

    obligation_retrieval_enabled: bool = True
    obligation_retrieval_concurrency: int = 4
    obligation_retrieval_max_queries: int = 4
    obligation_retrieval_union_top_k: int = 20
    obligation_retrieval_keep_best_fallback: bool = True
    obligation_retrieval_adaptive_ladder: bool = True
    obligation_retrieval_parallel_queries: bool = True
    obligation_retrieval_weak_expand_probe_only: bool = False
    obligation_relevance_use_lexical_categories: bool = True
    obligation_relevance_fallback_on_overlap_miss: bool = True

    evidence_sufficiency_enabled: bool = True
    evidence_min_hits: int = 1
    evidence_min_score: float = 0.35
    evidence_min_concept_overlap: float = 0.15
    evidence_min_doc_coverage: float = 0.0
    evidence_expand_max_rounds: int = 2
    evidence_expand_broaden_mode: Literal["concepts", "catalog_neighbor", "both"] = "both"
    evidence_expand_max_extra_docs: int = 3
    evidence_expand_concurrency: int = 4
    # PR-01 — defer lexical veto to compare LLM when rerank score is strong
    evidence_rerank_bypass_enabled: bool = True
    evidence_rerank_bypass_min_confidence: float = 0.55
    # When catalog match returns candidates, retrieve + gate on hits instead of hard IPC.
    evidence_compare_on_catalog_candidates: bool = True

    obligation_compare_enabled: bool = True
    obligation_compare_batch_size: int = 24
    obligation_compare_max_tokens: int = 48_000
    obligation_compare_max_obligation_chars: int = 3000
    # IPC0-R — v2 prompt gated until E-LLM1 experiment
    obligation_compare_prompt_v2_enabled: bool = False
    # IPC3 E-BP2 — boilerplate substantive override (default off)
    ipc3_boilerplate_substantive_override_enabled: bool = False
    # IPC3 E-EV1 — semantic overlap gate (default off; calibrate before enable)
    evidence_semantic_overlap_enabled: bool = False
    evidence_min_semantic_overlap: float = 0.72
    # IPC3 — defer low_routing_confidence IPC when rerank + catalog candidates are strong
    evidence_low_routing_rerank_defer_enabled: bool = False
    # IPC4 — catalog fenced strong + retrieval hits → compare (planner confidence not required)
    evidence_catalog_strong_defer_enabled: bool = True
    obligation_section_cutover_mode: Literal["skip", "legacy_parallel", "ipc_fallback"] = "ipc_fallback"

    routing_cache_enabled: bool = True
    routing_cache_ttl_seconds: int = 300
    routing_plan_cache_max_entries: int = 500
    max_obligations_per_review: int = 200
    max_obligations_per_section: int = 8
    obligation_cap_mode: Literal["round_robin", "sequential"] = "round_robin"
    max_planner_calls_per_review: int = 60
    max_catalog_search_calls_per_review: int = 150
    routing_planner_max_catalog_policies: int = 12
    obligation_routing_tenant_allowlist: str = ""
    obligation_routing_tenant_denylist: str = ""

    recovery_promote_obligation_ipc_gaps: bool = True

    guard_pass_enabled: bool = True
    guard_pass_mode: Literal["llm"] = "llm"
    guard_pass_concurrency: int = 2
    guard_pass_batch_size: int = 8
    guard_pass_non_compliant_only: bool = True
    guard_pass_max_tokens: int = 512
    guard_rationale_repair_enabled: bool = True
    guard_rationale_repair_batch_enabled: bool = True

    quote_repair_enabled: bool = True
    quote_repair_batch_enabled: bool = True
    quote_repair_batch_size: int = 6
    quote_repair_max_chars: int = 8_000
    quote_repair_max_tokens: int = 512
    grounding_downgrade_mode: Literal["inconclusive", "keep_status_flag"] = "keep_status_flag"

    @model_validator(mode="before")
    @classmethod
    def _migrate_section_classify_settings(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if "section_classify_lexical_fallback" in data and "section_classify_mode" not in data:
            if data["section_classify_lexical_fallback"] is False:
                data["section_classify_mode"] = "llm_only"
        return data


def _maybe_warn_discovery_cap(settings: ReviewSettings) -> None:
    global _config_cap_warned
    if _config_cap_warned:
        return
    _config_cap_warned = True
    if settings.discovery_max_policies > 0 and settings.discovery_group_cap_mode == "adaptive":
        logger.info(
            "discovery_max_policies=%s applies flat cap after group cap; "
            "enterprise deploys typically use 0",
            settings.discovery_max_policies,
        )


def _apply_rate_limit_profile(settings: ReviewSettings) -> ReviewSettings:
    """Optional Mistral-safe defaults; explicit env vars always win."""
    if settings.llm_rate_limit_profile != "mistral_conservative":
        return settings
    updates: dict[str, int | float] = {}
    if "LLM_GLOBAL_CONCURRENCY" not in os.environ:
        updates["llm_global_concurrency"] = 1
    if "LLM_RATE_LIMIT_MAX_RETRIES" not in os.environ:
        updates["llm_rate_limit_max_retries"] = 5
    if "LLM_RATE_LIMIT_BACKOFF_MAX_SECONDS" not in os.environ:
        updates["llm_rate_limit_backoff_max_seconds"] = 60.0
    if not updates:
        return settings
    return settings.model_copy(update=updates)


def build_runtime_settings_snapshot(
    review: ReviewSettings | None = None,
    core: Any | None = None,
) -> dict[str, str | int | float | bool]:
    """Non-secret resolved settings for ops reproducibility."""
    resolved = review or ReviewSettings()
    if core is None:
        from document_core.config import get_settings as get_core_settings

        core = get_core_settings()

    reranker_backend = core.reranker_backend if core.reranker_enabled else "off"
    return {
        "review_policy_scope": resolved.review_policy_scope,
        "discovery_group_mode": resolved.discovery_group_mode,
        "discovery_group_cap_mode": resolved.discovery_group_cap_mode,
        "discovery_max_policy_groups": resolved.discovery_max_policy_groups,
        "discovery_min_policy_groups": resolved.discovery_min_policy_groups,
        "discovery_max_policy_groups_ceiling": resolved.discovery_max_policy_groups_ceiling,
        "discovery_max_policies": resolved.discovery_max_policies,
        "discovery_max_topics_ceiling": resolved.discovery_max_topics_ceiling,
        "discovery_vendor_complete_threshold": resolved.discovery_vendor_complete_threshold,
        "section_classify_mode": resolved.section_classify_mode,
        "compare_policy_hit_mode": resolved.compare_policy_hit_mode,
        "compare_max_policy_hits": resolved.compare_max_policy_hits,
        "guard_pass_enabled": resolved.guard_pass_enabled,
        "guard_pass_batch_size": resolved.guard_pass_batch_size,
        "quote_repair_batch_enabled": resolved.quote_repair_batch_enabled,
        "quote_repair_batch_size": resolved.quote_repair_batch_size,
        "guard_rationale_repair_batch_enabled": resolved.guard_rationale_repair_batch_enabled,
        "llm_global_concurrency": resolved.llm_global_concurrency,
        "llm_rate_limit_max_retries": resolved.llm_rate_limit_max_retries,
        "llm_rate_limit_profile": resolved.llm_rate_limit_profile,
        "llm_review_posture_enabled": resolved.llm_review_posture_enabled,
        "llm_review_scope_reset_events": resolved.llm_review_scope_reset_events,
        "llm_hot_structure_split_max": resolved.llm_hot_structure_split_max,
        "llm_hot_acquire_pause_enabled": resolved.llm_hot_acquire_pause_enabled,
        "review_pipeline_mode": resolved.review_pipeline_mode,
        "compare_branch_fail_open": resolved.compare_branch_fail_open,
        "grounding_branch_fail_open": resolved.grounding_branch_fail_open,
        "obligation_section_cutover_mode": resolved.obligation_section_cutover_mode,
        "obligation_retrieval_skip_resolved_sections": resolved.obligation_retrieval_skip_resolved_sections,
        "obligation_skip_resolved_parallel_guard": resolved.obligation_skip_resolved_parallel_guard,
        "obligation_retrieval_section_hit_reuse": resolved.obligation_retrieval_section_hit_reuse,
        "mcp_search_cache_enabled": resolved.mcp_search_cache_enabled,
        "mcp_http_max_connections": resolved.mcp_http_max_connections,
        "mcp_global_concurrency": resolved.mcp_global_concurrency,
        "mcp_semaphore_acquire_timeout_seconds": resolved.mcp_semaphore_acquire_timeout_seconds,
        "mcp_semaphore_acquire_warn_seconds": resolved.mcp_semaphore_acquire_warn_seconds,
        "section_compare_batch_size": resolved.section_compare_batch_size,
        "compare_token_budget_mode": resolved.compare_token_budget_mode,
        "compare_token_pack_mode": resolved.compare_token_pack_mode,
        "playbook_compare_max_chars": resolved.playbook_compare_max_chars,
        "config_advisory_enabled": resolved.config_advisory_enabled,
        "final_verify_unclear_recompare_cap_mode": resolved.final_verify_unclear_recompare_cap_mode,
        "obligation_routing_enabled": resolved.obligation_routing_enabled,
        "obligation_routing_tenant_allowlist": resolved.obligation_routing_tenant_allowlist,
        "review_pipeline_tenant_allowlist": resolved.review_pipeline_tenant_allowlist,
        "compare_quote_anchor_enabled": resolved.compare_quote_anchor_enabled,
        "guard_pass_non_compliant_only": resolved.guard_pass_non_compliant_only,
        "policy_coverage_enabled": resolved.policy_coverage_enabled,
        "final_verify_unclear_recompare_enabled": resolved.final_verify_unclear_recompare_enabled,
        "final_verify_coverage_gate_recompare_enabled": (
            resolved.final_verify_coverage_gate_recompare_enabled
        ),
        "baseline_profile": resolved.baseline_profile or "",
        "routing_alias_min_score": resolved.routing_alias_min_score,
        "evidence_min_score": resolved.evidence_min_score,
        "evidence_min_concept_overlap": resolved.evidence_min_concept_overlap,
        "evidence_rerank_bypass_enabled": resolved.evidence_rerank_bypass_enabled,
        "catalog_match_max_candidates": resolved.catalog_match_max_candidates,
        "obligation_retrieval_union_top_k": resolved.obligation_retrieval_union_top_k,
        "routing_discovery_before_match": resolved.routing_discovery_before_match,
        "recovery_promote_obligation_ipc_gaps": resolved.recovery_promote_obligation_ipc_gaps,
        "retrieval_final_top_k": resolved.retrieval_final_top_k,
        "retrieval_category_hard_filter": resolved.retrieval_category_hard_filter,
        "retrieval_meaning_first_enabled": resolved.retrieval_meaning_first_enabled,
        "retrieval_section_query_max_chars": resolved.retrieval_section_query_max_chars,
        "compare_hit_allow_primary_fallback": resolved.compare_hit_allow_primary_fallback,
        "reranker_enabled": core.reranker_enabled,
        "reranker_backend": reranker_backend,
    }


_settings_cache: ReviewSettings | None = None
_settings_cached_at: float = 0.0
_SETTINGS_TTL = float(os.getenv("SETTINGS_CACHE_TTL_SECONDS", "30"))


def get_settings() -> ReviewSettings:
    global _settings_cache, _settings_cached_at
    now = time.monotonic()
    if _settings_cache is not None and (now - _settings_cached_at) < _SETTINGS_TTL:
        return _settings_cache
    settings = _apply_rate_limit_profile(ReviewSettings())
    _maybe_warn_discovery_cap(settings)
    _settings_cache = settings
    _settings_cached_at = now
    return settings


def _clear_settings_cache() -> None:
    global _settings_cache, _settings_cached_at
    _settings_cache = None
    _settings_cached_at = 0.0


get_settings.cache_clear = _clear_settings_cache  # type: ignore[attr-defined]
