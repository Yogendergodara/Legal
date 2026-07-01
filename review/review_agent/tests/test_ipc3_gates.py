"""IPC-3 funnel identity, boilerplate override, compare prompt loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from review_agent.config import ReviewSettings
from review_agent.schemas.obligation import ContractObligation
from review_agent.schemas.routing_plan import CatalogMatchResult, ObligationRoutingPlan
from review_agent.services.catalog_matcher import match_obligation_to_catalog
from review_agent.services.compare_prompt_loader import (
    active_obligation_compare_prompt_label,
    obligation_compare_prompt_path,
)
from review_agent.services.ipc3_gates import (
    boilerplate_obligation_routable,
    boilerplate_substantive_override,
    check_obligation_funnel_identity,
)
from review_agent.services.obligation_retrieval import retrieve_for_obligation

_PROMPTS = Path(__file__).resolve().parent.parent / "review_agent" / "prompts"


def _ob(**kwargs) -> ContractObligation:
    defaults = dict(
        obligation_id="s1-o0",
        section_id="s1",
        text="Customer must notify within 72 hours.",
        obligation_type="general",
        explicit_policy_mentions=[],
    )
    defaults.update(kwargs)
    return ContractObligation(**defaults)


def test_funnel_identity_ok():
    stats = {
        "obligation_count": 66,
        "obligation_pipeline_funnel": {
            "extracted": 66,
            "compare_queued": 29,
            "compare_pre_ipc": 37,
            "llm_ipc_count": 14,
            "post_validation_compared": 15,
            "llm_items_returned": 29,
            "skip_by_reason": {
                "evidence_sufficient": 29,
                "boilerplate": 17,
                "routing_or_skip": 14,
                "low_concept_overlap": 2,
                "low_relevance_score": 3,
                "insufficient_evidence": 1,
            },
        },
    }
    assert check_obligation_funnel_identity(stats) == []


def test_funnel_identity_detects_break():
    stats = {
        "obligation_pipeline_funnel": {
            "extracted": 66,
            "compare_queued": 30,
            "compare_pre_ipc": 37,
            "llm_ipc_count": 14,
            "post_validation_compared": 15,
            "llm_items_returned": 29,
        },
    }
    errors = check_obligation_funnel_identity(stats)
    assert any("PRE_IPC+QUEUED" in e for e in errors)


def test_boilerplate_override_off_by_default():
    ob = _ob(explicit_policy_mentions=["DPA"])
    plan = ObligationRoutingPlan(
        obligation_id=ob.obligation_id,
        routing_source="skipped_boilerplate",
        confidence=0.0,
    )
    cfg = ReviewSettings(ipc3_boilerplate_substantive_override_enabled=False)
    assert boilerplate_substantive_override(ob, plan, cfg) is False


def test_boilerplate_override_explicit_mention():
    ob = _ob(explicit_policy_mentions=["atlassian-dpa"])
    plan = ObligationRoutingPlan(
        obligation_id=ob.obligation_id,
        routing_source="skipped_boilerplate",
        confidence=0.0,
    )
    cfg = ReviewSettings(ipc3_boilerplate_substantive_override_enabled=True)
    assert boilerplate_substantive_override(ob, plan, cfg) is True


@pytest.mark.asyncio
async def test_catalog_matcher_boilerplate_override_runs_search():
    ob = _ob(explicit_policy_mentions=["atlassian-dpa"])
    plan = ObligationRoutingPlan(
        obligation_id=ob.obligation_id,
        routing_source="skipped_boilerplate",
        confidence=0.0,
        intent="data processing",
    )

    class _Client:
        async def search_policy_catalog(self, _req):
            return []

    cfg = ReviewSettings(ipc3_boilerplate_substantive_override_enabled=True)
    match = await match_obligation_to_catalog(
        plan,
        client=_Client(),
        tenant_id="t1",
        catalog_entries=[],
        allowed_doc_ids=set(),
        settings=cfg,
        obligation=ob,
    )
    assert match.route_decision != "ipc" or match.candidate_doc_ids == []


@pytest.mark.asyncio
async def test_retrieval_boilerplate_override_skips_bundle():
    ob = _ob()
    plan = ObligationRoutingPlan(
        obligation_id=ob.obligation_id,
        routing_source="skipped_boilerplate",
        confidence=0.0,
    )
    match = CatalogMatchResult(obligation_id=ob.obligation_id, route_decision="ipc")
    cfg = ReviewSettings(ipc3_boilerplate_substantive_override_enabled=False)

    class _Client:
        pass

    bundle = await retrieve_for_obligation(
        _Client(),
        obligation=ob,
        plan=plan,
        match=match,
        tenant_id="t1",
        contract_type=None,
        policy_type=None,
        settings=cfg,
    )
    assert bundle.skipped_reason == "boilerplate"


def test_boilerplate_obligation_routable_substantive_type():
    ob = _ob(obligation_type="privacy", is_boilerplate=True)
    cfg = ReviewSettings(ipc3_boilerplate_substantive_override_enabled=True)
    assert boilerplate_obligation_routable(ob, cfg) is True


def test_boilerplate_obligation_routable_general_stays_skipped():
    ob = _ob(obligation_type="general", is_boilerplate=True)
    cfg = ReviewSettings(ipc3_boilerplate_substantive_override_enabled=True)
    assert boilerplate_obligation_routable(ob, cfg) is False


@pytest.mark.asyncio
async def test_catalog_matcher_low_confidence_uses_obligation_explicit_mentions():
    ob = _ob(explicit_policy_mentions=["atlassian-dpa"])
    plan = ObligationRoutingPlan(
        obligation_id=ob.obligation_id,
        routing_source="planner_fallback",
        confidence=0.4,
        intent="data processing",
        explicit_policy_mentions=[],
    )

    class _Client:
        async def search_policy_catalog(self, _req):
            return []

    cfg = ReviewSettings()
    match = await match_obligation_to_catalog(
        plan,
        client=_Client(),
        tenant_id="t1",
        catalog_entries=[],
        allowed_doc_ids=set(),
        settings=cfg,
        obligation=ob,
    )
    assert match.queries_used


def test_compare_prompt_loader_defaults_v1():
    cfg = ReviewSettings(obligation_compare_prompt_v2_enabled=False)
    assert active_obligation_compare_prompt_label(cfg) == "v1"
    assert obligation_compare_prompt_path(cfg).name == "obligation_compare_v1.md"
    assert obligation_compare_prompt_path(cfg).is_file()


def test_compare_prompt_loader_v2_when_enabled():
    cfg = ReviewSettings(obligation_compare_prompt_v2_enabled=True)
    assert active_obligation_compare_prompt_label(cfg) == "v2"
    assert obligation_compare_prompt_path(cfg).name == "obligation_compare_v2.md"


def test_v1_prompt_single_obligation_wording():
    text = (_PROMPTS / "obligation_compare_v1.md").read_text(encoding="utf-8")
    assert "single contract obligation" in text.lower()
    assert "up to 24" not in text


def test_v2_prompt_batch_wording():
    text = (_PROMPTS / "obligation_compare_v2.md").read_text(encoding="utf-8")
    assert "up to 24" in text
