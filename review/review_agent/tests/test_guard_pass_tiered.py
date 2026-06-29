"""Tests for tiered rationale guard (P2-6 / P1 batch)."""

from __future__ import annotations

import re

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
        rationale="Contract cap is extremely low and materially unfavorable.",
        grounded=True,
        metadata={"source": "playbook_compare", "review_guidance": "Cap should track fees."},
    )
    return base.model_copy(update=overrides)


def _batch_guard_result(user: str, *, support_level: SupportLevel, reason: str = "ok"):
    ids = re.findall(r"finding_id:\s*(\S+)", user) or ["f1"]
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
async def test_guard_inference_ok_keeps_non_compliant(monkeypatch):
    async def _fake_invoke(_model, schema, *, system, user):
        if schema is BatchRationaleGuardLLMResult:
            return _batch_guard_result(
                user,
                support_level=SupportLevel.INFERENCE_OK,
                reason="Evaluative judgment from quoted term difference.",
            )
        return RationaleGuardResult(
            support_level=SupportLevel.INFERENCE_OK,
            reason="Evaluative judgment from quoted term difference.",
        )

    monkeypatch.setattr("review_agent.services.guard_pass.invoke_structured", _fake_invoke)
    monkeypatch.setattr("review_agent.services.guard_pass.get_review_model", lambda **_: object())

    updated, warnings, stats = await run_guard_pass(
        [_finding()],
        settings=ReviewSettings(guard_pass_enabled=True),
    )
    assert stats["guard_checked"] == 1
    assert stats["guard_inference_ok"] == 1
    assert stats["guard_failed"] == 0
    assert updated[0].status == ComplianceStatus.NON_COMPLIANT
    assert updated[0].grounded is True
    assert updated[0].metadata.get("guard_support_level") == SupportLevel.INFERENCE_OK.value
    assert not warnings


@pytest.mark.asyncio
async def test_guard_unsupported_triggers_repair(monkeypatch):
    guard_calls = {"n": 0}

    async def _fake_invoke(_model, schema, *, system, user):
        guard_calls["n"] += 1
        if schema is BatchRationaleGuardLLMResult:
            if guard_calls["n"] == 1:
                return _batch_guard_result(
                    user,
                    support_level=SupportLevel.UNSUPPORTED,
                    reason="Fabricated amount.",
                )
            return _batch_guard_result(
                user,
                support_level=SupportLevel.FULL,
                reason="ok",
            )
        if guard_calls["n"] == 1:
            return RationaleGuardResult(
                support_level=SupportLevel.UNSUPPORTED,
                reason="Fabricated amount.",
            )
        return RationaleGuardResult(support_level=SupportLevel.FULL, reason="ok")

    async def _fake_repair_batch(findings, *, settings=None):
        return {
            f.finding_id: "Contract uses a three-month fees basis versus twelve months in policy."
            for f in findings
        }

    monkeypatch.setattr("review_agent.services.guard_pass.invoke_structured", _fake_invoke)
    monkeypatch.setattr(
        "review_agent.services.guard_pass.repair_rationales_batch",
        _fake_repair_batch,
    )
    monkeypatch.setattr("review_agent.services.guard_pass.get_review_model", lambda **_: object())

    updated, _warnings, stats = await run_guard_pass(
        [_finding()],
        settings=ReviewSettings(
            guard_pass_enabled=True,
            guard_rationale_repair_enabled=True,
        ),
    )
    assert stats["guard_repair_attempts"] == 1
    assert stats["guard_repair_success"] == 1
    assert stats["guard_failed"] == 0
    assert updated[0].status == ComplianceStatus.NON_COMPLIANT
    assert updated[0].metadata.get("guard_repair_success") is True


@pytest.mark.asyncio
async def test_guard_unsupported_downgrades_after_repair_fail(monkeypatch):
    async def _fake_invoke(_model, schema, *, system, user):
        if schema is BatchRationaleGuardLLMResult:
            return _batch_guard_result(
                user,
                support_level=SupportLevel.UNSUPPORTED,
                reason="Hallucinated fact.",
            )
        return RationaleGuardResult(
            support_level=SupportLevel.UNSUPPORTED,
            reason="Hallucinated fact.",
        )

    async def _fake_repair_batch(findings, *, settings=None):
        return {f.finding_id: "Still cites $5M cap not in quotes." for f in findings}

    monkeypatch.setattr("review_agent.services.guard_pass.invoke_structured", _fake_invoke)
    monkeypatch.setattr(
        "review_agent.services.guard_pass.repair_rationales_batch",
        _fake_repair_batch,
    )
    monkeypatch.setattr("review_agent.services.guard_pass.get_review_model", lambda **_: object())

    updated, warnings, stats = await run_guard_pass(
        [_finding()],
        settings=ReviewSettings(
            guard_pass_enabled=True,
            guard_rationale_repair_enabled=True,
        ),
    )
    assert stats["guard_failed"] == 1
    assert stats["guard_repair_attempts"] == 1
    assert stats["guard_repair_success"] == 0
    assert updated[0].status == ComplianceStatus.INCONCLUSIVE
    assert updated[0].metadata.get("guard_failed") is True
    assert warnings


@pytest.mark.asyncio
async def test_guard_does_not_clear_grounded_on_inference_ok(monkeypatch):
    async def _fake_invoke(_model, schema, *, system, user):
        if schema is BatchRationaleGuardLLMResult:
            return _batch_guard_result(
                user,
                support_level=SupportLevel.INFERENCE_OK,
                reason="Professional inference.",
            )
        return RationaleGuardResult(
            support_level=SupportLevel.INFERENCE_OK,
            reason="Professional inference.",
        )

    monkeypatch.setattr("review_agent.services.guard_pass.invoke_structured", _fake_invoke)
    monkeypatch.setattr("review_agent.services.guard_pass.get_review_model", lambda **_: object())

    original = _finding(grounded=True)
    updated, _warnings, stats = await run_guard_pass(
        [original],
        settings=ReviewSettings(guard_pass_enabled=True),
    )
    assert stats["guard_failed"] == 0
    assert updated[0].grounded is True
