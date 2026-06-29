"""Tests for post-grounding rationale guard (P6.1 / P2-6 / P1 batch)."""

from __future__ import annotations

import re
from unittest.mock import AsyncMock

import pytest
from document_core.schemas.compliance import ComplianceFinding, ComplianceStatus, Severity

from review_agent.config import ReviewSettings
from review_agent.schemas.guard_llm import (
    BatchRationaleGuardLLMResult,
    RationaleGuardBatchItem,
    RationaleGuardResult,
    SupportLevel,
)
from review_agent.services.guard_pass import run_guard_pass


def _finding(**overrides) -> ComplianceFinding:
    base = ComplianceFinding(
        finding_id="f1",
        dimension_id="s1:cap",
        dimension_label="Liability Cap",
        status=ComplianceStatus.NON_COMPLIANT,
        severity=Severity.CRITICAL,
        contract_quote="fees paid in three months",
        policy_quote="fees paid in twelve months",
        contract_section_id="s1",
        rationale="Contract cap is below the policy minimum.",
        grounded=True,
        metadata={"source": "playbook_compare"},
    )
    return base.model_copy(update=overrides)


def _finding_ids_from_user(user: str) -> list[str]:
    return re.findall(r"finding_id:\s*(\S+)", user)


def _batch_guard_result(
    user: str,
    *,
    support_level: SupportLevel,
    reason: str = "ok",
) -> BatchRationaleGuardLLMResult:
    ids = _finding_ids_from_user(user) or ["f1"]
    return BatchRationaleGuardLLMResult(
        items=[
            RationaleGuardBatchItem(
                finding_id=fid,
                support_level=support_level,
                reason=reason,
            )
            for fid in ids
        ]
    )


@pytest.mark.asyncio
async def test_guard_downgrades_when_not_supported(monkeypatch):
    async def _fake_invoke(_model, schema, *, system, user):
        if schema is BatchRationaleGuardLLMResult:
            return _batch_guard_result(
                user,
                support_level=SupportLevel.UNSUPPORTED,
                reason="Rationale overstates gap.",
            )
        return RationaleGuardResult(
            support_level=SupportLevel.UNSUPPORTED,
            reason="Rationale overstates gap.",
        )

    monkeypatch.setattr("review_agent.services.guard_pass.invoke_structured", _fake_invoke)
    monkeypatch.setattr("review_agent.services.guard_pass.get_review_model", lambda **_: object())
    monkeypatch.setattr(
        "review_agent.services.guard_pass.repair_rationale_for_finding",
        AsyncMock(side_effect=AssertionError("repair should not run")),
    )

    updated, warnings, stats = await run_guard_pass(
        [_finding()],
        settings=ReviewSettings(
            guard_pass_enabled=True,
            guard_rationale_repair_enabled=False,
        ),
    )
    assert stats["guard_checked"] == 1
    assert stats["guard_failed"] == 1
    assert stats["guard_batch_calls"] == 1
    assert updated[0].status == ComplianceStatus.INCONCLUSIVE
    assert updated[0].metadata.get("guard_failed") is True
    assert warnings


@pytest.mark.asyncio
async def test_guard_keeps_supported_finding(monkeypatch):
    async def _fake_invoke(_model, schema, *, system, user):
        if schema is BatchRationaleGuardLLMResult:
            return _batch_guard_result(user, support_level=SupportLevel.FULL)
        return RationaleGuardResult(support_level=SupportLevel.FULL, reason="ok")

    monkeypatch.setattr("review_agent.services.guard_pass.invoke_structured", _fake_invoke)
    monkeypatch.setattr("review_agent.services.guard_pass.get_review_model", lambda **_: object())

    original = _finding()
    updated, _warnings, stats = await run_guard_pass(
        [original],
        settings=ReviewSettings(guard_pass_enabled=True),
    )
    assert stats["guard_failed"] == 0
    assert updated[0].status == ComplianceStatus.NON_COMPLIANT
    assert updated[0].metadata.get("guard_failed") is None


@pytest.mark.asyncio
async def test_guard_skips_insufficient_policy_context():
    finding = _finding(status=ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT)
    updated, _warnings, stats = await run_guard_pass(
        [finding],
        settings=ReviewSettings(guard_pass_enabled=True),
    )
    assert stats["guard_skipped"] == 1
    assert stats["guard_checked"] == 0
    assert updated[0].status == ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT


@pytest.mark.asyncio
async def test_guard_skips_compliant_when_non_compliant_only(monkeypatch):
    compliant = _finding(
        finding_id="f-compliant",
        status=ComplianceStatus.COMPLIANT,
        severity=Severity.INFO,
        rationale="Contract meets policy requirements.",
    )
    violation = _finding(finding_id="f-nc")

    call_count = {"n": 0}

    async def _fake_invoke(_model, schema, *, system, user):
        call_count["n"] += 1
        if schema is BatchRationaleGuardLLMResult:
            return _batch_guard_result(user, support_level=SupportLevel.FULL)
        return RationaleGuardResult(support_level=SupportLevel.FULL, reason="ok")

    monkeypatch.setattr("review_agent.services.guard_pass.invoke_structured", _fake_invoke)
    monkeypatch.setattr("review_agent.services.guard_pass.get_review_model", lambda **_: object())

    updated, _warnings, stats = await run_guard_pass(
        [compliant, violation],
        settings=ReviewSettings(
            guard_pass_enabled=True,
            guard_pass_non_compliant_only=True,
        ),
    )

    assert stats["guard_checked"] == 1
    assert stats["guard_skipped"] == 1
    assert call_count["n"] == 1
    assert updated[0].metadata.get("guard_support_level") is None
    assert updated[1].metadata.get("guard_support_level") == SupportLevel.FULL.value


@pytest.mark.asyncio
async def test_guard_batch_one_call_for_multiple_findings(monkeypatch):
    findings = [
        _finding(finding_id="f1", dimension_label="Cap A"),
        _finding(finding_id="f2", dimension_label="Cap B"),
        _finding(finding_id="f3", dimension_label="Cap C"),
    ]
    call_count = {"n": 0}

    async def _fake_invoke(_model, schema, *, system, user):
        call_count["n"] += 1
        assert schema is BatchRationaleGuardLLMResult
        return _batch_guard_result(user, support_level=SupportLevel.FULL)

    monkeypatch.setattr("review_agent.services.guard_pass.invoke_structured", _fake_invoke)
    monkeypatch.setattr("review_agent.services.guard_pass.get_review_model", lambda **_: object())

    updated, _warnings, stats = await run_guard_pass(
        findings,
        settings=ReviewSettings(
            guard_pass_enabled=True,
            guard_pass_batch_size=4,
        ),
    )
    assert call_count["n"] == 1
    assert stats["guard_batch_calls"] == 1
    assert stats["guard_checked"] == 3
    assert all(f.metadata.get("guard_support_level") == SupportLevel.FULL.value for f in updated)


@pytest.mark.asyncio
async def test_guard_disabled_is_noop():
    updated, warnings, stats = await run_guard_pass(
        [_finding()],
        settings=ReviewSettings(guard_pass_enabled=False),
    )
    assert updated[0].status == ComplianceStatus.NON_COMPLIANT
    assert stats["guard_skipped"] == 1
    assert not warnings


def test_should_guard_ungrounded_non_compliant_kept():
    from review_agent.services.guard_pass import _should_guard

    finding = _finding(
        grounded=False,
        metadata={"source": "playbook_compare", "grounding_failed": True},
    )
    assert _should_guard(
        finding,
        ReviewSettings(guard_pass_non_compliant_only=True),
    )


def test_should_guard_inconclusive_prior_non_compliant():
    from review_agent.services.guard_pass import _should_guard

    finding = _finding(
        status=ComplianceStatus.INCONCLUSIVE,
        grounded=False,
        metadata={
            "source": "playbook_compare",
            "grounding_failed": True,
            "prior_status": "NON_COMPLIANT",
        },
    )
    assert _should_guard(
        finding,
        ReviewSettings(guard_pass_non_compliant_only=True),
    )
