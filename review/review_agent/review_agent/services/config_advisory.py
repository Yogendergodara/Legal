"""Operator config advisories — warn on LLM-multiplying settings without blocking review (Phase E)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from review_agent.config import ReviewSettings
from review_agent.services.pipeline_mode import parallel_pipeline_active
from review_agent.services.routing_tenant import _parse_tenant_list, obligation_routing_active

AdvisorySeverity = Literal["info", "warn"]


@dataclass(frozen=True)
class ConfigAdvisory:
    rule_id: str
    severity: AdvisorySeverity
    message: str


def effective_unclear_recompare_max_sections(
    settings: ReviewSettings,
    *,
    reviewable_sections: int,
) -> int:
    """Dynamic tail cap — scales with contract size; never zero when eligible exist."""
    fixed = max(0, settings.final_verify_unclear_recompare_max_sections)
    if settings.final_verify_unclear_recompare_cap_mode == "fixed":
        return fixed
    if reviewable_sections <= 0:
        return fixed
    scaled = max(2, math.ceil(reviewable_sections * 0.20))
    return min(fixed, scaled)


def evaluate_config_advisories(
    settings: ReviewSettings,
    *,
    tenant_id: str,
    reviewable_sections: int | None = None,
) -> list[ConfigAdvisory]:
    """Return non-fatal advisories for operator misconfiguration (E1–E7)."""
    if not settings.config_advisory_enabled:
        return []

    advisories: list[ConfigAdvisory] = []
    tenant = (tenant_id or "").strip()

    if settings.section_classify_mode == "llm_only":
        advisories.append(
            ConfigAdvisory(
                rule_id="E1",
                severity="warn",
                message=(
                    "SECTION_CLASSIFY_MODE=llm_only disables lexical-first skips; "
                    "expect full classify LLM cost — use lexical_first in production"
                ),
            )
        )

    routing_allow = _parse_tenant_list(settings.obligation_routing_tenant_allowlist)
    routing_deny = _parse_tenant_list(settings.obligation_routing_tenant_denylist)
    if settings.obligation_routing_enabled:
        if tenant == "e2e-demo":
            advisories.append(
                ConfigAdvisory(
                    rule_id="E2d",
                    severity="warn",
                    message=(
                        "Legacy shared tenant e2e-demo — use atlassian-demo/xecurify-demo; "
                        "routing should be disabled via OBLIGATION_ROUTING_TENANT_DENYLIST"
                    ),
                )
            )
        if routing_allow and tenant and tenant in routing_allow:
            advisories.append(
                ConfigAdvisory(
                    rule_id="E2b",
                    severity="warn" if not routing_deny else "info",
                    message=(
                        "Obligation routing enabled for this tenant (pilot allowlist)"
                        + (
                            " — add OBLIGATION_ROUTING_TENANT_DENYLIST for legacy tenants"
                            if not routing_deny
                            else ""
                        )
                    ),
                )
            )
        elif not routing_allow:
            advisories.append(
                ConfigAdvisory(
                    rule_id="E2",
                    severity="warn",
                    message=(
                        "OBLIGATION_ROUTING_ENABLED=true with empty tenant allowlist — "
                        "hybrid extract/planner/compare runs for all tenants"
                    ),
                )
            )

    parallel_allow = _parse_tenant_list(settings.review_pipeline_tenant_allowlist)
    if parallel_pipeline_active(tenant, settings) and not parallel_allow:
        advisories.append(
            ConfigAdvisory(
                rule_id="E3",
                severity="warn",
                message=(
                    "REVIEW_PIPELINE_MODE=parallel_hybrid without tenant allowlist — "
                    "parallel compare burst may increase 429 rate; use serial for production"
                ),
            )
        )

    if (
        settings.llm_global_concurrency > 3
        and settings.llm_rate_limit_profile != "mistral_conservative"
    ):
        advisories.append(
            ConfigAdvisory(
                rule_id="E4",
                severity="warn",
                message=(
                    f"LLM_GLOBAL_CONCURRENCY={settings.llm_global_concurrency} without "
                    "mistral_conservative profile — consider lower concurrency or conservative profile"
                ),
            )
        )

    if (
        settings.llm_rate_limit_profile == "default"
        and settings.llm_global_concurrency >= 2
    ):
        advisories.append(
            ConfigAdvisory(
                rule_id="E10",
                severity="warn",
                message=(
                    "LLM_RATE_LIMIT_PROFILE=default with LLM_GLOBAL_CONCURRENCY>=2 — "
                    "use mistral_conservative for golden/battery runs"
                ),
            )
        )

    if settings.section_compare_concurrency > settings.llm_global_concurrency:
        advisories.append(
            ConfigAdvisory(
                rule_id="E4b",
                severity="warn",
                message=(
                    f"SECTION_COMPARE_CONCURRENCY={settings.section_compare_concurrency} exceeds "
                    f"LLM_GLOBAL_CONCURRENCY={settings.llm_global_concurrency} — "
                    "compare batches will queue on the global semaphore"
                ),
            )
        )

    if (
        reviewable_sections is not None
        and reviewable_sections >= 15
        and settings.max_obligations_per_review > 80
    ):
        advisories.append(
            ConfigAdvisory(
                rule_id="E8",
                severity="warn",
                message=(
                    f"MAX_OBLIGATIONS_PER_REVIEW={settings.max_obligations_per_review} on "
                    f"{reviewable_sections}-section contract — use 80 for golden-scale "
                    "Atlassian runs to match P5 cap behavior"
                ),
            )
        )

    if (
        reviewable_sections is not None
        and settings.final_verify_unclear_recompare_cap_mode == "fixed"
        and settings.final_verify_unclear_recompare_max_sections >= 8
        and reviewable_sections < 20
    ):
        advisories.append(
            ConfigAdvisory(
                rule_id="E5",
                severity="info",
                message=(
                    f"Fixed unclear recompare cap ({settings.final_verify_unclear_recompare_max_sections}) "
                    f"on {reviewable_sections}-section contract — consider adaptive cap mode"
                ),
            )
        )

    if settings.guard_pass_enabled and not settings.guard_pass_non_compliant_only:
        advisories.append(
            ConfigAdvisory(
                rule_id="E6",
                severity="warn",
                message=(
                    "GUARD_PASS_NON_COMPLIANT_ONLY=false — guard LLM runs on a broader finding set"
                ),
            )
        )

    if settings.quote_repair_enabled and not settings.compare_quote_anchor_enabled:
        advisories.append(
            ConfigAdvisory(
                rule_id="E7",
                severity="warn",
                message=(
                    "QUOTE_REPAIR_ENABLED with COMPARE_QUOTE_ANCHOR_ENABLED=false — "
                    "expect elevated quote repair LLM; enable anchoring instead of disabling repair"
                ),
            )
        )

    advisories.extend(_evaluate_accuracy_protect_advisories(settings))

    return advisories


def _evaluate_accuracy_protect_advisories(settings: ReviewSettings) -> list[ConfigAdvisory]:
    """Phase F — warn when accuracy-first paths are disabled (never block review)."""
    advisories: list[ConfigAdvisory] = []

    if not settings.policy_coverage_enabled:
        advisories.append(
            ConfigAdvisory(
                rule_id="F1-off",
                severity="warn",
                message=(
                    "POLICY_COVERAGE_ENABLED=false — coverage gate off; "
                    "compare may run on weak/off-topic hits (false NC risk)"
                ),
            )
        )

    if not settings.final_verify_unclear_recompare_enabled:
        advisories.append(
            ConfigAdvisory(
                rule_id="F5-off",
                severity="warn",
                message=(
                    "FINAL_VERIFY_UNCLEAR_RECOMPARE_ENABLED=false — "
                    "tail recovery compare disabled (429/low-confidence misses)"
                ),
            )
        )

    if not settings.final_verify_coverage_gate_recompare_enabled:
        advisories.append(
            ConfigAdvisory(
                rule_id="F5b-off",
                severity="warn",
                message=(
                    "FINAL_VERIFY_COVERAGE_GATE_RECOMPARE_ENABLED=false — "
                    "coverage_gate_ipc rows will not recompare in final verify"
                ),
            )
        )

    if settings.evidence_min_score < 0.25 or settings.evidence_min_concept_overlap < 0.15:
        advisories.append(
            ConfigAdvisory(
                rule_id="F4-risk",
                severity="info",
                message=(
                    "Evidence gate thresholds very permissive "
                    f"(score={settings.evidence_min_score}, "
                    f"overlap={settings.evidence_min_concept_overlap}) — golden required"
                ),
            )
        )

    return advisories


def format_config_advisory_warnings(advisories: list[ConfigAdvisory]) -> list[str]:
    return [
        f"config_advisory:{adv.severity}:{adv.rule_id}:{adv.message}" for adv in advisories
    ]


def build_config_pressure_diagnosis(
    *,
    settings: ReviewSettings,
    tenant_id: str,
    compliance_stats: dict,
    reviewable_sections: int,
    advisories: list[ConfigAdvisory] | None = None,
) -> dict:
    """Ops-facing config pressure block for engine_diagnosis."""
    advs = advisories or evaluate_config_advisories(
        settings,
        tenant_id=tenant_id,
        reviewable_sections=reviewable_sections,
    )
    sections = max(reviewable_sections, 1)
    quote_attempts = int(compliance_stats.get("quote_repair_attempts") or 0)
    guard_repairs = int(compliance_stats.get("guard_repair_attempts") or 0)
    nc_count = int(compliance_stats.get("non_compliant_count") or 0)
    if nc_count == 0:
        nc_count = int(compliance_stats.get("guard_checked") or 0)

    quote_ratio = round(quote_attempts / sections, 3)
    guard_ratio = round(guard_repairs / max(nc_count, 1), 3)

    flags: list[str] = []
    if quote_attempts > 0 and quote_ratio >= 0.5:
        flags.append("quote_pressure")
    if guard_repairs > 0 and guard_ratio >= 0.5:
        flags.append("guard_pressure")
    if any(a.rule_id == "E1" and a.severity == "warn" for a in advs):
        flags.append("classify_llm_only")
    if any(a.rule_id in ("E2", "E3", "E4") for a in advs):
        flags.append("config_llm_burst_risk")

    return {
        "advisories": [
            {"rule_id": a.rule_id, "severity": a.severity, "message": a.message} for a in advs
        ],
        "classify_lexical_skipped": int(compliance_stats.get("classify_lexical_skipped") or 0),
        "classify_llm_sections": int(compliance_stats.get("classify_llm_sections") or 0),
        "classify_boilerplate_skipped": int(
            compliance_stats.get("classify_boilerplate_skipped") or 0
        ),
        "quote_repair_attempts": quote_attempts,
        "quote_repair_per_section": quote_ratio,
        "guard_repair_attempts": guard_repairs,
        "guard_repair_per_nc": guard_ratio,
        "unclear_recompare_cap_mode": settings.final_verify_unclear_recompare_cap_mode,
        "unclear_recompare_cap_fixed": settings.final_verify_unclear_recompare_max_sections,
        "unclear_recompare_cap_effective": effective_unclear_recompare_max_sections(
            settings,
            reviewable_sections=reviewable_sections,
        ),
        "llm_rate_limit_profile": settings.llm_rate_limit_profile,
        "llm_global_concurrency_effective": settings.llm_global_concurrency,
        "section_classify_mode": settings.section_classify_mode,
        "obligation_routing_active": obligation_routing_active(tenant_id, settings),
        "parallel_pipeline_active": parallel_pipeline_active(tenant_id, settings),
        "pressure_flags": flags,
    }
