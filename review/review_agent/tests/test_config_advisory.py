"""Tests for Phase E config advisory engine."""

from __future__ import annotations

from review_agent.config import ReviewSettings
from review_agent.services.config_advisory import (
    effective_unclear_recompare_max_sections,
    evaluate_config_advisories,
    format_config_advisory_warnings,
)


def test_production_defaults_no_warnings_for_pilot_tenant():
    settings = ReviewSettings(
        obligation_routing_enabled=False,
        review_pipeline_mode="serial",
        section_classify_mode="lexical_first",
        llm_rate_limit_profile="mistral_conservative",
        llm_global_concurrency=2,
        policy_coverage_enabled=True,
        final_verify_unclear_recompare_enabled=True,
        final_verify_coverage_gate_recompare_enabled=True,
    )
    advisories = evaluate_config_advisories(settings, tenant_id="e2e-demo")
    assert not [a for a in advisories if a.severity == "warn"]


def test_e1_llm_only_warns():
    settings = ReviewSettings(section_classify_mode="llm_only")
    advisories = evaluate_config_advisories(settings, tenant_id="demo")
    assert any(a.rule_id == "E1" for a in advisories)


def test_e2_global_routing_warns():
    settings = ReviewSettings(
        obligation_routing_enabled=True,
        obligation_routing_tenant_allowlist="",
    )
    advisories = evaluate_config_advisories(settings, tenant_id="demo")
    assert any(a.rule_id == "E2" for a in advisories)


def test_e2b_allowlisted_tenant_warns_without_denylist():
    settings = ReviewSettings(
        obligation_routing_enabled=True,
        obligation_routing_tenant_allowlist="demo",
    )
    advisories = evaluate_config_advisories(settings, tenant_id="demo")
    assert any(a.rule_id == "E2b" and a.severity == "warn" for a in advisories)
    assert not any(a.rule_id == "E2" for a in advisories)


def test_e2b_allowlisted_tenant_info_with_denylist():
    settings = ReviewSettings(
        obligation_routing_enabled=True,
        obligation_routing_tenant_allowlist="demo",
        obligation_routing_tenant_denylist="legacy",
    )
    advisories = evaluate_config_advisories(settings, tenant_id="demo")
    assert any(a.rule_id == "E2b" and a.severity == "info" for a in advisories)


def test_e2d_legacy_shared_tenant_warns():
    settings = ReviewSettings(obligation_routing_enabled=True)
    advisories = evaluate_config_advisories(settings, tenant_id="e2e-demo")
    assert any(a.rule_id == "E2d" for a in advisories)


def test_e3_parallel_without_allowlist_warns():
    settings = ReviewSettings(review_pipeline_mode="parallel_hybrid")
    advisories = evaluate_config_advisories(settings, tenant_id="demo")
    assert any(a.rule_id == "E3" for a in advisories)


def test_e4_high_concurrency_warns():
    settings = ReviewSettings(llm_global_concurrency=5)
    advisories = evaluate_config_advisories(settings, tenant_id="demo")
    assert any(a.rule_id == "E4" for a in advisories)


def test_e10_default_profile_with_concurrency_two():
    settings = ReviewSettings(llm_rate_limit_profile="default", llm_global_concurrency=2)
    advisories = evaluate_config_advisories(settings, tenant_id="demo")
    assert any(a.rule_id == "E10" for a in advisories)


def test_e4b_compare_concurrency_exceeds_global():
    settings = ReviewSettings(llm_global_concurrency=2, section_compare_concurrency=4)
    advisories = evaluate_config_advisories(settings, tenant_id="demo")
    assert any(a.rule_id == "E4b" for a in advisories)


def test_e7_quote_repair_without_anchor():
    settings = ReviewSettings(quote_repair_enabled=True, compare_quote_anchor_enabled=False)
    advisories = evaluate_config_advisories(settings, tenant_id="demo")
    assert any(a.rule_id == "E7" for a in advisories)


def test_advisory_disabled():
    settings = ReviewSettings(section_classify_mode="llm_only", config_advisory_enabled=False)
    assert evaluate_config_advisories(settings, tenant_id="demo") == []


def test_f1_off_advisory_when_coverage_disabled():
    advisories = evaluate_config_advisories(
        ReviewSettings(policy_coverage_enabled=False),
        tenant_id="demo",
    )
    assert any(a.rule_id == "F1-off" for a in advisories)


def test_f5_off_advisory():
    advisories = evaluate_config_advisories(
        ReviewSettings(final_verify_unclear_recompare_enabled=False),
        tenant_id="demo",
    )
    assert any(a.rule_id == "F5-off" for a in advisories)


def test_format_warnings_prefix():
    advisories = evaluate_config_advisories(
        ReviewSettings(section_classify_mode="llm_only"),
        tenant_id="demo",
    )
    warnings = format_config_advisory_warnings(advisories)
    assert warnings[0].startswith("config_advisory:warn:E1:")


def test_e8_uncapped_obligations_warns_on_large_contract():
    settings = ReviewSettings(max_obligations_per_review=200)
    advisories = evaluate_config_advisories(settings, tenant_id="demo", reviewable_sections=20)
    assert any(a.rule_id == "E8" for a in advisories)


def test_adaptive_unclear_cap_small_contract():
    settings = ReviewSettings(
        final_verify_unclear_recompare_max_sections=8,
        final_verify_unclear_recompare_cap_mode="adaptive",
    )
    assert effective_unclear_recompare_max_sections(settings, reviewable_sections=10) == 2


def test_adaptive_unclear_cap_large_contract():
    settings = ReviewSettings(
        final_verify_unclear_recompare_max_sections=8,
        final_verify_unclear_recompare_cap_mode="adaptive",
    )
    assert effective_unclear_recompare_max_sections(settings, reviewable_sections=60) == 8


def test_fixed_cap_unchanged():
    settings = ReviewSettings(
        final_verify_unclear_recompare_max_sections=4,
        final_verify_unclear_recompare_cap_mode="fixed",
    )
    assert effective_unclear_recompare_max_sections(settings, reviewable_sections=10) == 4
