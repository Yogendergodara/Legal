"""Unit tests for LLM compliance (mocked — no live API)."""

from uuid import uuid4

import pytest
from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk, RetrievalHit
from document_core.schemas.compliance import ComplianceStatus, Severity

from review_agent.schemas.compliance_llm import ComplianceLLMResult
from review_agent.services import compliance_llm


def _hit(text: str, *, kind: DocumentKind) -> RetrievalHit:
    doc_id = uuid4()
    chunk = IndexedChunk(
        chunk_id="c1",
        document_id=doc_id,
        tenant_id="demo",
        kind=kind,
        chunk_role=ChunkRole.PARENT,
        section_id="s1",
        section_path="1",
        title="Section",
        text=text,
    )
    return RetrievalHit(parent_chunk=chunk, score=1.0)


@pytest.mark.asyncio
async def test_no_policy_hits_skips_llm(monkeypatch):
    called = False

    async def _fake_invoke(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("LLM should not be called")

    monkeypatch.setattr(compliance_llm, "invoke_structured", _fake_invoke)
    finding = await compliance_llm.compare_sections_llm(
        dimension_id="liability",
        dimension_label="Liability",
        contract_hits=[_hit("contract text", kind=DocumentKind.CONTRACT)],
        policy_hits=[],
    )
    assert finding.status == ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT
    assert finding.metadata.get("llm_skipped") is True
    assert called is False


@pytest.mark.asyncio
async def test_llm_non_compliant_mapped(monkeypatch):
    policy_text = "Liability shall not exceed twelve months fees."
    contract_text = "Liability is unlimited for all claims."

    async def _fake_invoke(_model, _schema, *, system, user):
        assert "Policy section" in user
        assert policy_text in user
        assert contract_text in user
        assert "invent" in system.lower() or "do not" in system.lower()
        return ComplianceLLMResult(
            status=ComplianceStatus.NON_COMPLIANT,
            severity=Severity.CRITICAL,
            contract_quote="Liability is unlimited for all claims.",
            policy_quote="Liability shall not exceed twelve months fees.",
            rationale="Contract removes cap required by policy section 4.1.",
            confidence=0.9,
        )

    monkeypatch.setattr(compliance_llm, "get_review_model", lambda **_: object())
    monkeypatch.setattr(compliance_llm, "invoke_structured", _fake_invoke)

    finding = await compliance_llm.compare_sections_llm(
        dimension_id="liability",
        dimension_label="Liability",
        contract_hits=[_hit(contract_text, kind=DocumentKind.CONTRACT)],
        policy_hits=[_hit(policy_text, kind=DocumentKind.POLICY)],
    )
    assert finding.status == ComplianceStatus.NON_COMPLIANT
    assert finding.severity == Severity.CRITICAL
    assert "twelve months" in finding.policy_quote


@pytest.mark.asyncio
async def test_invalid_quotes_downgraded_to_inconclusive(monkeypatch):
    policy_text = "Liability shall not exceed twelve months fees."
    contract_text = "Liability is unlimited."

    async def _fake_invoke(_model, _schema, *, system, user):
        return ComplianceLLMResult(
            status=ComplianceStatus.NON_COMPLIANT,
            severity=Severity.CRITICAL,
            contract_quote="this quote is not in the contract",
            policy_quote="also not in policy",
            rationale="Should be downgraded due to invalid quotes.",
            confidence=0.5,
        )

    monkeypatch.setattr(compliance_llm, "get_review_model", lambda **_: object())
    monkeypatch.setattr(compliance_llm, "invoke_structured", _fake_invoke)

    finding = await compliance_llm.compare_sections_llm(
        dimension_id="liability",
        dimension_label="Liability",
        contract_hits=[_hit(contract_text, kind=DocumentKind.CONTRACT)],
        policy_hits=[_hit(policy_text, kind=DocumentKind.POLICY)],
    )
    assert finding.status == ComplianceStatus.INCONCLUSIVE
    assert "Downgraded" in finding.rationale


@pytest.mark.asyncio
async def test_llm_failure_returns_inconclusive(monkeypatch):
    from review_agent.config import ReviewSettings

    async def _fake_invoke(*_args, **_kwargs):
        raise ValueError("model unavailable")

    settings = ReviewSettings(compliance_mode="llm", compliance_llm_max_retries=0)
    monkeypatch.setattr(compliance_llm, "get_settings", lambda: settings)
    monkeypatch.setattr(compliance_llm, "get_review_model", lambda **_: object())
    monkeypatch.setattr(compliance_llm, "invoke_structured", _fake_invoke)

    finding = await compliance_llm.compare_sections_llm(
        dimension_id="liability",
        dimension_label="Liability",
        contract_hits=[_hit("Liability is unlimited.", kind=DocumentKind.CONTRACT)],
        policy_hits=[_hit("Liability shall not exceed twelve months fees.", kind=DocumentKind.POLICY)],
    )
    assert finding.status == ComplianceStatus.INCONCLUSIVE
    assert finding.metadata.get("llm_error")
