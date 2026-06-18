"""Review agent runtime configuration."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class ReviewSettings(BaseSettings):
    """Settings for compliance review (lexical fallback + LLM mode)."""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    compliance_mode: Literal["lexical", "llm", "hybrid"] = "llm"
    compliance_llm_temperature: float = 0.0
    compliance_max_section_chars: int = 12_000
    compliance_llm_max_retries: int = 1
    compliance_llm_role: str = "reasoning"
    compliance_llm_max_tokens: int = 2048

    compliance_batch_size: int = 6
    compliance_prescreen_enabled: bool = True
    compliance_prescreen_compliant_min: float = 0.35
    compliance_prescreen_noncompliant_max: float = 0.05
    compliance_retrieval_score_min: float = 0.15
    compliance_gap_pass_enabled: bool = True
    compliance_llm_concurrency: int = 3
    compliance_retrieval_concurrency: int = 10

    review_plan_mode: Literal["dynamic", "static"] = "dynamic"
    review_max_categories: int = 30
    review_min_section_chars: int = 40

    policy_catalog_url: str | None = None
    policy_fetch_enabled: bool = True
    policy_search_top_k: int = 5

    review_plan_llm_filter: bool = False
    review_plan_llm_filter_min_categories: int = 15
    review_plan_llm_temperature: float = 0.0
    review_plan_llm_max_retries: int = 1
    review_plan_llm_max_tokens: int = 1024

    review_policy_scope: Literal["request", "tenant", "discovered"] = "request"

    review_policy_source: Literal["request", "tenant_auto"] = "request"
    contract_routing_mode: Literal["llm", "lexical"] = "llm"
    contract_routing_max_chars: int = 12_000
    discovery_max_policies: int = 8
    discovery_top_k_per_topic: int = 3
    discovery_min_score: float = 0.08

    # Phase 10 — section-first pipeline
    review_pipeline_mode: Literal["legacy", "section_first"] = "legacy"
    section_classify_mode: Literal["llm", "lexical"] = "lexical"
    section_classify_max_chars: int = 12_000
    retrieval_recall_top_k: int = 20
    retrieval_final_top_k: int = 10
    section_compare_batch_size: int = 2
    section_compare_max_tokens: int = 48_000
    section_retrieval_concurrency: int = 8
    section_compare_concurrency: int = 3


@lru_cache
def get_settings() -> ReviewSettings:
    return ReviewSettings()
