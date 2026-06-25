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

    section_classify_batch_size: int = 2
    section_classify_batch_size_on_parse_fail: int = 1
    section_classify_max_chars: int = 12_000
    section_classify_mode: Literal["lexical_first", "llm_only"] = "lexical_first"
    section_classify_batch_retry_single: bool = True
    section_lexical_body_scan_chars: int = 800
    section_lexical_full_body_max_chars: int = 4000
    section_classify_block_general_substantive: bool = True
    section_cross_ref_enabled: bool = True
    section_compare_context_max_chars: int = 3000
    gap_boilerplate_skip_compare: bool = True
    retrieval_recall_top_k: int = 20
    retrieval_final_top_k: int = 10
    retrieval_max_attempts: int = 3
    retrieval_broaden_on_retry: bool = True
    retrieval_category_hard_filter: bool = True
    retrieval_category_filter_fallback: bool = True
    retrieval_skip_hard_filter_for_general: bool = True
    retrieval_max_hits_per_document: int = 3
    named_policy_routing_enabled: bool = True
    retrieval_relevance_gate_enabled: bool = True
    retrieval_relevance_min_score: float = 0.2
    policy_coverage_enabled: bool = True
    policy_coverage_min_score: float = 0.34
    incorporation_guard_enabled: bool = True
    equivalence_guard_enabled: bool = True
    finding_dedupe_topic_cluster: bool = True
    retrieval_category_min_overlap: int = 0
    discovery_warn_on_cap: bool = True
    section_compare_batch_size: int = 2
    section_compare_max_findings_per_section: int = 4
    finding_dedupe_across_policies: bool = True
    section_compare_max_tokens: int = 48_000
    section_compare_max_section_chars: int = 32_000
    section_retrieval_concurrency: int = 8
    section_compare_concurrency: int = 2
    compare_policy_hit_mode: Literal["all_top_k", "category_aligned", "primary_only"] = "category_aligned"
    compare_max_policy_hits: int = 2
    compare_hit_min_relevance_score: float = 0.35
    compare_batch_retry_single: bool = True
    compare_quote_anchor_enabled: bool = True

    final_gap_verify_enabled: bool = True
    final_gap_recall_top_k: int = 30
    final_verify_unclear_recompare_enabled: bool = True
    final_verify_unclear_recompare_max_sections: int = 4

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

    playbook_enrich_compare: bool = True
    playbook_load_registry: bool = False
    grounding_downgrade_not_drop: bool = True
    grounding_rerun_coverage: bool = True
    grounding_relax_compliant_empty_policy: bool = True
    conflict_emit_on_skip: bool = False

    artifact_include_hit_refs: bool = True
    artifact_max_hit_refs_per_section: int = 10
    report_llm_summary: bool = False
    report_llm_summary_max_tokens: int = 256

    guard_pass_enabled: bool = True
    guard_pass_mode: Literal["llm"] = "llm"
    guard_pass_concurrency: int = 2
    guard_pass_batch_size: int = 4
    guard_pass_non_compliant_only: bool = True
    guard_pass_max_tokens: int = 512
    guard_rationale_repair_enabled: bool = True

    quote_repair_enabled: bool = True
    quote_repair_max_chars: int = 8_000
    quote_repair_max_tokens: int = 512
    grounding_downgrade_mode: Literal["inconclusive", "keep_status_flag"] = "inconclusive"

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
        "section_classify_mode": resolved.section_classify_mode,
        "compare_policy_hit_mode": resolved.compare_policy_hit_mode,
        "compare_max_policy_hits": resolved.compare_max_policy_hits,
        "guard_pass_enabled": resolved.guard_pass_enabled,
        "guard_pass_batch_size": resolved.guard_pass_batch_size,
        "llm_global_concurrency": resolved.llm_global_concurrency,
        "llm_rate_limit_max_retries": resolved.llm_rate_limit_max_retries,
        "retrieval_final_top_k": resolved.retrieval_final_top_k,
        "retrieval_category_hard_filter": resolved.retrieval_category_hard_filter,
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
    settings = ReviewSettings()
    _maybe_warn_discovery_cap(settings)
    _settings_cache = settings
    _settings_cached_at = now
    return settings


def _clear_settings_cache() -> None:
    global _settings_cache, _settings_cached_at
    _settings_cache = None
    _settings_cached_at = 0.0


get_settings.cache_clear = _clear_settings_cache  # type: ignore[attr-defined]
