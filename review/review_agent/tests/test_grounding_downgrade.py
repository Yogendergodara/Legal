"""Tests for grounding downgrade + post-grounding coverage (P4.4)."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from document_core.schemas.chunk import (
    ChunkRole,
    DocumentKind,
    GroundingCheckResult,
    IngestResult,
    IndexedChunk,
    StructureConfidence,
)
from document_core.schemas.compliance import ComplianceFinding, ComplianceStatus, Severity
from review_agent.config import ReviewSettings
from review_agent.graph.nodes import grounding_node
from review_agent.services.grounding_quote import verify_quote_with_repair


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
        contract_quote="bad quote not in doc",
        policy_quote="policy quote",
        contract_section_id="s1",
        policy_document_id=uuid4(),
        rationale="Cap too low.",
        metadata={"source": "playbook_compare"},
    )
    return base.model_copy(update=overrides)


@pytest.mark.asyncio
async def test_grounding_downgrades_instead_of_drop(monkeypatch):
    monkeypatch.setattr(
        "review_agent.graph.nodes.get_settings",
        lambda: ReviewSettings(
            grounding_downgrade_not_drop=True,
            grounding_downgrade_mode="inconclusive",
            grounding_rerun_coverage=False,
            quote_repair_enabled=False,
            guard_pass_enabled=False,
        ),
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
    assert grounded[0].metadata.get("prior_status") == ComplianceStatus.NON_COMPLIANT.value
    assert grounded[0].contract_quote == ""


@pytest.mark.asyncio
async def test_grounding_keep_status_flag_default(monkeypatch):
    monkeypatch.setattr(
        "review_agent.graph.nodes.get_settings",
        lambda: ReviewSettings(
            grounding_rerun_coverage=False,
            quote_repair_enabled=False,
            guard_pass_enabled=False,
        ),
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

    state = {
        "tenant_id": "demo",
        "ingest_result": _ingest_result(),
        "findings": [_finding()],
        "indexed_policies": [],
    }
    result = await grounding_node(state, client)
    grounded = result["grounded_findings"]
    assert len(grounded) == 1
    assert grounded[0].status == ComplianceStatus.NON_COMPLIANT
    assert grounded[0].metadata.get("grounding_failed") is True
    assert grounded[0].grounded is False


@pytest.mark.asyncio
async def test_grounding_rerun_coverage_backfill(monkeypatch):
    monkeypatch.setattr(
        "review_agent.graph.nodes.get_settings",
        lambda: ReviewSettings(
            grounding_downgrade_not_drop=True,
            grounding_rerun_coverage=True,
            enforce_section_coverage=True,
            review_min_section_chars=10,
            guard_pass_enabled=False,
        ),
    )
    client = MagicMock()
    client.verify_quote = AsyncMock(
        return_value=GroundingCheckResult(
            grounded=True,
            quote="ok",
            normalized_quote="ok",
        )
    )
    client.verify_policy_quote = AsyncMock(
        return_value=GroundingCheckResult(
            grounded=True,
            quote="ok",
            normalized_quote="ok",
        )
    )

    section = IndexedChunk(
        chunk_id="c1",
        document_id=uuid4(),
        tenant_id="demo",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id="s2",
        section_path="s2",
        title="Uncovered",
        text="This section has enough text to be reviewable.",
    )
    state = {
        "tenant_id": "demo",
        "ingest_result": _ingest_result(),
        "findings": [],
        "indexed_policies": [],
        "section_review_sections": [section.model_dump(mode="json")],
    }
    result = await grounding_node(state, client)
    coverage = result.get("section_coverage") or {}
    assert coverage.get("post_grounding_backfill_count", 0) >= 1
    assert any(
        f.contract_section_id == "s2"
        for f in result["grounded_findings"]
    )


@pytest.mark.asyncio
async def test_grounding_quote_rejects_section_mismatch():
    async def _verify_mismatch(_request):
        return GroundingCheckResult(
            grounded=True,
            quote="Managed security services",
            normalized_quote="managed security services",
            section_id="8",
        )

    quote, ok, meta = await verify_quote_with_repair(
        MagicMock(),
        tenant_id="demo",
        document_id=uuid4(),
        quote="Managed security services",
        section_id="3",
        settings=ReviewSettings(quote_repair_enabled=False),
        stats={},
        verify_fn=_verify_mismatch,
    )
    assert quote == "Managed security services"
    assert ok is False
    assert meta.get("grounding_section_mismatch") is True
    assert meta.get("grounding_matched_section_id") == "8"


@pytest.mark.asyncio
async def test_grounding_keeps_compliant_with_empty_policy_quote(monkeypatch):
    monkeypatch.setattr(
        "review_agent.graph.nodes.get_settings",
        lambda: ReviewSettings(
            grounding_downgrade_not_drop=True,
            grounding_rerun_coverage=False,
            grounding_relax_compliant_empty_policy=True,
            quote_repair_enabled=False,
            guard_pass_enabled=False,
        ),
    )
    client = MagicMock()
    client.verify_quote = AsyncMock(
        return_value=GroundingCheckResult(
            grounded=True,
            quote="Support and respect internationally proclaimed human rights",
            normalized_quote="support and respect internationally proclaimed human rights",
        )
    )
    client.verify_policy_quote = AsyncMock()

    state = {
        "tenant_id": "demo",
        "ingest_result": _ingest_result(),
        "findings": [
            _finding(
                status=ComplianceStatus.COMPLIANT,
                severity=Severity.INFO,
                contract_quote="Support and respect internationally proclaimed human rights",
                policy_quote="",
                rationale="Contract aligns with the policy human rights requirement.",
            )
        ],
        "indexed_policies": [],
    }
    result = await grounding_node(state, client)
    grounded = result["grounded_findings"]
    assert len(grounded) == 1
    assert grounded[0].status == ComplianceStatus.COMPLIANT
    assert grounded[0].grounded is True
