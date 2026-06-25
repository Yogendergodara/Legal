"""Golden routing tests + wrong_policy_compare CI gate (Phase R8)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from review_agent.config import ReviewSettings
from review_agent.services.routing_golden_harness import (
    load_catalog_fixture,
    load_golden_cases,
    run_all_golden_cases,
    run_routing_pipeline_for_obligation,
    wrong_policy_compare_count,
)


@pytest.fixture
def routing_settings() -> ReviewSettings:
    return ReviewSettings(
        obligation_routing_enabled=True,
        semantic_planner_enabled=True,
        obligation_retrieval_enabled=True,
        evidence_sufficiency_enabled=True,
        evidence_min_score=0.35,
        routing_ipc_max_confidence=0.60,
    )


@pytest.fixture
def xecurify_catalog():
    entries, version = load_catalog_fixture()
    return entries, version


def _case(obligation_id: str) -> dict:
    for case in load_golden_cases():
        if case["obligation_id"] == obligation_id:
            return case
    raise KeyError(obligation_id)


@pytest.mark.routing_golden
@pytest.mark.asyncio
async def test_golden_ipc_boilerplate(routing_settings, xecurify_catalog):
    entries, _ = xecurify_catalog
    for obligation_id in ("10.1-o0", "10.5-o0", "10.6-o0", "9.1-o0", "1.1-o0"):
        case = _case(obligation_id)
        result = await run_routing_pipeline_for_obligation(
            case,
            catalog_entries=entries,
            client=AsyncMock(),
            settings=routing_settings,
        )
        assert result.match.route_decision == "ipc"
        assert result.evidence.decision == "ipc"
        assert result.finding_status == "INSUFFICIENT_POLICY_CONTEXT"
        assert "29356d10-36dc-5ef8-8cf1-a2948f7c2e28" not in result.candidate_doc_ids


@pytest.mark.routing_golden
@pytest.mark.asyncio
async def test_golden_alias_security_practices(routing_settings, xecurify_catalog):
    entries, _ = xecurify_catalog
    case = _case("2.3-o0")
    result = await run_routing_pipeline_for_obligation(
        case,
        catalog_entries=entries,
        client=AsyncMock(),
        settings=routing_settings,
    )
    assert result.plan.routing_source == "registry_alias"
    assert result.candidate_doc_ids == ["cb031cc8-f40e-58e7-87bb-7a315dc61051"]
    assert result.evidence.decision == "compare"


@pytest.mark.routing_golden
@pytest.mark.asyncio
async def test_golden_tenant_fence(routing_settings, xecurify_catalog):
    entries, _ = xecurify_catalog
    case = _case("fence-o0")
    result = await run_routing_pipeline_for_obligation(
        case,
        catalog_entries=entries,
        client=AsyncMock(),
        settings=routing_settings,
    )
    assert "00000000-0000-4000-8000-000000009999" not in result.candidate_doc_ids
    assert "29356d10-36dc-5ef8-8cf1-a2948f7c2e28" in result.candidate_doc_ids


@pytest.mark.routing_golden
@pytest.mark.asyncio
async def test_golden_synthetic_cyber_defense_manual(routing_settings):
    entries, _ = load_catalog_fixture("synthetic_weird_catalog.json")
    case = _case("syn-cdm-o0")
    result = await run_routing_pipeline_for_obligation(
        case,
        catalog_entries=entries,
        client=AsyncMock(),
        settings=routing_settings,
    )
    assert result.candidate_doc_ids == ["a1000001-0000-4000-8000-000000000001"]
    assert result.evidence.decision == "compare"


@pytest.mark.routing_golden
@pytest.mark.asyncio
async def test_golden_wrong_policy_gate_zero(routing_settings):
    results = await run_all_golden_cases(settings=routing_settings)
    assert len(results) >= 22
    assert wrong_policy_compare_count(results) == 0


@pytest.mark.routing_golden
def test_golden_fixture_count():
    assert len(load_golden_cases()) >= 22


def assert_golden_gate() -> None:
    """CI entrypoint — raises AssertionError when gate fails."""
    import asyncio

    results = asyncio.run(run_all_golden_cases())
    count = wrong_policy_compare_count(results)
    if count != 0:
        offenders = [r.obligation_id for r in results if r.forbidden_violations]
        raise AssertionError(f"wrong_policy_compare_count={count}, offenders={offenders}")


@pytest.mark.routing_golden
def test_regression_flag_off_smoke():
    settings = ReviewSettings(obligation_routing_enabled=False)
    assert settings.obligation_routing_enabled is False
