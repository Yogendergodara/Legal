"""Tests for LLM quote repair and grounding integration (P2-7)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from document_core.schemas.chunk import (
    ChunkRole,
    DocumentKind,
    GroundingCheckResult,
    IndexedChunk,
    IngestResult,
    StructureConfidence,
)
from document_core.schemas.compliance import ComplianceFinding, ComplianceStatus, Severity
from review_agent.config import ReviewSettings
from review_agent.graph.nodes import grounding_node
from review_agent.schemas.quote_repair import QuoteRepairResult
from review_agent.services.quote_repair_llm import repair_quote_for_section
from review_agent.services.quote_validate import quote_is_substring


def _ingest_result() -> IngestResult:
    return IngestResult(
        document_id=uuid4(),
        tenant_id="demo",
        kind=DocumentKind.CONTRACT,
        title="MSA",
        parent_count=1,
        child_count=0,
        structure_confidence=StructureConfidence.HIGH,
    )


def _finding(**overrides) -> ComplianceFinding:
    base = ComplianceFinding(
        finding_id="f1",
        dimension_id="s1:liability",
        dimension_label="Liability Cap",
        status=ComplianceStatus.NON_COMPLIANT,
        severity=Severity.CRITICAL,
        contract_quote="bad paraphrase",
        policy_quote="policy quote",
        contract_section_id="s1",
        policy_document_id=uuid4(),
        rationale="Cap too low.",
        metadata={"source": "playbook_compare"},
    )
    return base.model_copy(update=overrides)


@pytest.mark.asyncio
async def test_repair_finds_verbatim_span(monkeypatch):
    section_text = "The total liability shall not exceed $100,000 for consequential damages."

    async def _fake_invoke(_model, schema, *, system, user):
        return QuoteRepairResult(
            repaired_quote="$100,000 for consequential damages",
            confidence=0.9,
            repair_notes="matched span",
        )

    monkeypatch.setattr(
        "review_agent.services.quote_repair_llm.invoke_structured",
        _fake_invoke,
    )
    monkeypatch.setattr(
        "review_agent.services.quote_repair_llm.get_review_model",
        lambda **_: object(),
    )

    result = await repair_quote_for_section(
        source_text=section_text,
        candidate_quote="bad paraphrase about $100,000 damages",
        section_id="s1",
        settings=ReviewSettings(quote_repair_enabled=True),
    )
    assert quote_is_substring(result.repaired_quote, section_text)
    assert result.repaired_quote == "$100,000 for consequential damages"


@pytest.mark.asyncio
async def test_repair_rejects_non_substring_llm_output(monkeypatch):
    section_text = "Fees paid in twelve months."

    async def _fake_invoke(_model, schema, *, system, user):
        return QuoteRepairResult(repaired_quote="invented text not in section")

    monkeypatch.setattr(
        "review_agent.services.quote_repair_llm.invoke_structured",
        _fake_invoke,
    )
    monkeypatch.setattr(
        "review_agent.services.quote_repair_llm.get_review_model",
        lambda **_: object(),
    )

    result = await repair_quote_for_section(
        source_text=section_text,
        candidate_quote="fees in 12 months",
        section_id="s1",
        settings=ReviewSettings(quote_repair_enabled=True),
    )
    assert result.repaired_quote == ""
    assert "non-substring" in result.repair_notes


@pytest.mark.asyncio
async def test_grounding_node_uses_repair_before_verify(monkeypatch):
    section = IndexedChunk(
        chunk_id="c1",
        document_id=uuid4(),
        tenant_id="demo",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id="s1",
        section_path="s1",
        title="Liability",
        text="The total liability shall not exceed $100,000.",
    )
    verify_calls: list[str] = []

    async def _verify(request):
        verify_calls.append(request.quote)
        if request.quote == "$100,000":
            return GroundingCheckResult(
                grounded=True,
                quote=request.quote,
                normalized_quote=request.quote,
            )
        return GroundingCheckResult(
            grounded=False,
            quote=request.quote,
            normalized_quote=request.quote,
        )

    async def _fake_repair(**kwargs):
        return QuoteRepairResult(repaired_quote="$100,000", repair_notes="fixed")

    monkeypatch.setattr(
        "review_agent.graph.nodes.get_settings",
        lambda: ReviewSettings(
            grounding_downgrade_not_drop=True,
            grounding_rerun_coverage=False,
            quote_repair_enabled=True,
            guard_pass_enabled=False,
        ),
    )
    async def _fake_repair_batch(jobs, *, settings=None, stats=None):
        from review_agent.schemas.quote_repair import QuoteRepairResult

        return {
            job.repair_id: QuoteRepairResult(repaired_quote="$100,000", repair_notes="fixed")
            for job in jobs
        }

    monkeypatch.setattr(
        "review_agent.services.grounding_quote.repair_quotes_batch",
        _fake_repair_batch,
    )

    client = MagicMock()
    client.verify_quote = AsyncMock(side_effect=_verify)
    client.verify_policy_quote = AsyncMock(
        return_value=GroundingCheckResult(
            grounded=True,
            quote="policy quote",
            normalized_quote="policy quote",
        )
    )
    client.get_section = AsyncMock(return_value=section)

    state = {
        "tenant_id": "demo",
        "ingest_result": _ingest_result(),
        "findings": [_finding(contract_quote="paraphrased $100,000 cap")],
        "indexed_policies": [],
    }
    result = await grounding_node(state, client)
    grounded = result["grounded_findings"]
    assert len(grounded) == 1
    assert grounded[0].status == ComplianceStatus.NON_COMPLIANT
    assert grounded[0].grounded is True
    assert grounded[0].contract_quote == "$100,000"
    assert grounded[0].metadata.get("quote_repair_used") is True
    assert result["compliance_stats"]["quote_repair_success"] == 1
    assert len(verify_calls) >= 2


@pytest.mark.asyncio
async def test_no_repair_when_already_grounded(monkeypatch):
    monkeypatch.setattr(
        "review_agent.graph.nodes.get_settings",
        lambda: ReviewSettings(
            grounding_downgrade_not_drop=True,
            grounding_rerun_coverage=False,
            quote_repair_enabled=True,
            guard_pass_enabled=False,
        ),
    )
    repair_mock = AsyncMock(return_value={})
    monkeypatch.setattr(
        "review_agent.services.grounding_quote.repair_quotes_batch",
        repair_mock,
    )

    client = MagicMock()
    client.verify_quote = AsyncMock(
        return_value=GroundingCheckResult(
            grounded=True,
            quote="exact quote",
            normalized_quote="exact quote",
        )
    )
    client.verify_policy_quote = AsyncMock(
        return_value=GroundingCheckResult(
            grounded=True,
            quote="policy quote",
            normalized_quote="policy quote",
        )
    )

    state = {
        "tenant_id": "demo",
        "ingest_result": _ingest_result(),
        "findings": [_finding(contract_quote="exact quote")],
        "indexed_policies": [],
    }
    result = await grounding_node(state, client)
    repair_mock.assert_not_called()
    assert result["grounded_findings"][0].grounded is True
    assert result["compliance_stats"].get("quote_repair_attempts", 0) == 0


@pytest.mark.asyncio
async def test_downgrade_when_repair_and_verify_fail(monkeypatch):
    section = IndexedChunk(
        chunk_id="c1",
        document_id=uuid4(),
        tenant_id="demo",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id="s1",
        section_path="s1",
        title="Liability",
        text="Some unrelated section text here.",
    )

    async def _fake_repair_batch(jobs, *, settings=None, stats=None):
        from review_agent.schemas.quote_repair import QuoteRepairResult

        return {
            job.repair_id: QuoteRepairResult(repaired_quote="", repair_notes="no match")
            for job in jobs
        }

    monkeypatch.setattr(
        "review_agent.graph.nodes.get_settings",
        lambda: ReviewSettings(
            grounding_downgrade_not_drop=True,
            grounding_downgrade_mode="inconclusive",
            grounding_rerun_coverage=False,
            quote_repair_enabled=True,
            guard_pass_enabled=False,
        ),
    )
    monkeypatch.setattr(
        "review_agent.services.grounding_quote.repair_quotes_batch",
        _fake_repair_batch,
    )

    client = MagicMock()
    client.verify_quote = AsyncMock(
        return_value=GroundingCheckResult(
            grounded=False,
            quote="bad quote not in doc",
            normalized_quote="bad quote not in doc",
        )
    )
    client.verify_policy_quote = AsyncMock(
        return_value=GroundingCheckResult(
            grounded=True,
            quote="policy quote",
            normalized_quote="policy quote",
        )
    )
    client.get_section = AsyncMock(return_value=section)

    state = {
        "tenant_id": "demo",
        "ingest_result": _ingest_result(),
        "findings": [_finding()],
        "indexed_policies": [],
    }
    result = await grounding_node(state, client)
    grounded = result["grounded_findings"]
    assert len(grounded) == 1
    assert grounded[0].status == ComplianceStatus.INCONCLUSIVE
    assert grounded[0].metadata.get("grounding_failed") is True
    assert result["compliance_stats"]["quote_repair_attempts"] == 1
