"""PR-01 — bootstrap + contract-run path must resolve precision funnel settings."""

from __future__ import annotations

import os

import bootstrap_env


def _simulate_contract_run_env(monkeypatch):
    """Mirror run_atlassian_review.py / run_live_contract_battery.py startup."""
    for key in list(os.environ):
        if key.startswith(
            (
                "EVIDENCE_",
                "CATALOG_",
                "OBLIGATION_RETRIEVAL_",
                "OBLIGATION_COMPARE_",
                "PLAYBOOK_",
                "COMPARE_MAX_",
                "ROUTING_PLANNER_",
                "RETRIEVAL_MEANING_",
                "OBLIGATION_SKIP_",
                "OBLIGATION_RETRIEVAL_SKIP_",
                "MAX_CATALOG_",
            )
        ):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("PR01_PRECISION_OPT_OUT", raising=False)
    bootstrap_env.load_env()
    bootstrap_env.apply_golden_review_defaults()
    bootstrap_env.setup_pythonpath()
    from review_agent.config import get_settings

    get_settings.cache_clear()


def test_pr01_bootstrap_resolves_rerank_bypass(monkeypatch):
    _simulate_contract_run_env(monkeypatch)
    from review_agent.config import get_settings

    s = get_settings()
    assert s.evidence_rerank_bypass_enabled is True
    assert s.evidence_rerank_bypass_min_confidence == 0.55


def test_pr01_bootstrap_resolves_catalog_and_retrieval(monkeypatch):
    _simulate_contract_run_env(monkeypatch)
    from review_agent.config import get_settings

    s = get_settings()
    assert s.catalog_match_top_k == 12
    assert s.catalog_match_max_candidates == 8
    assert s.obligation_retrieval_union_top_k == 20
    assert s.obligation_retrieval_max_queries == 4
    assert s.evidence_expand_max_rounds == 2
    assert s.evidence_expand_broaden_mode == "both"


def test_pr01_bootstrap_resolves_ob_and_sr01(monkeypatch):
    _simulate_contract_run_env(monkeypatch)
    from review_agent.config import get_settings

    s = get_settings()
    assert s.obligation_retrieval_skip_resolved_sections is False
    assert s.obligation_skip_resolved_parallel_guard is True
    assert s.retrieval_meaning_first_enabled is True
    assert s.retrieval_category_hard_filter is False
    assert s.review_pipeline_mode == "parallel_hybrid"


def test_catalog_keys_load_from_review_agent_env(monkeypatch):
    assert bootstrap_env._should_load_review_env_key("CATALOG_MATCH_TOP_K")
