"""Tests for grounding fail-open on tail-path errors (RC-14)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from document_core.schemas.chunk import (
    DocumentKind,
    IngestResult,
    StructureConfidence,
)
from document_core.schemas.compliance import ComplianceFinding, ComplianceStatus, Severity
from review_agent.config import ReviewSettings
from review_agent.graph.nodes import grounding_node


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


def _finding() -> ComplianceFinding:
    return ComplianceFinding(
        finding_id="f1",
        dimension_id="s1:liability",
        dimension_label="Liability Cap",
        status=ComplianceStatus.NON_COMPLIANT,
        severity=Severity.CRITICAL,
        contract_quote="bad quote",
        policy_quote="policy quote",
        contract_section_id="s1",
        policy_document_id=uuid4(),
        rationale="Cap too low.",
        metadata={"source": "playbook_compare"},
    )


@pytest.mark.asyncio
async def test_grounding_node_fail_open_on_repair_crash(monkeypatch):
    monkeypatch.setattr(
        "review_agent.graph.nodes.get_settings",
        lambda: ReviewSettings(
            grounding_branch_fail_open=True,
            grounding_rerun_coverage=False,
            guard_pass_enabled=False,
        ),
    )
    monkeypatch.setattr(
        "review_agent.graph.nodes.ground_findings_quotes",
        AsyncMock(side_effect=RuntimeError("429 rate limit exceeded")),
    )

    state = {
        "tenant_id": "demo",
        "ingest_result": _ingest_result(),
        "findings": [_finding()],
        "indexed_policies": [],
        "compliance_stats": {},
    }
    result = await grounding_node(state, MagicMock())
    assert len(result["grounded_findings"]) == 1
    assert result["compliance_stats"]["grounding_fail_open"] is True
    assert "fail-open" in result["warnings"][0]


@pytest.mark.asyncio
async def test_grounding_node_raises_when_fail_open_disabled(monkeypatch):
    monkeypatch.setattr(
        "review_agent.graph.nodes.get_settings",
        lambda: ReviewSettings(grounding_branch_fail_open=False),
    )
    monkeypatch.setattr(
        "review_agent.graph.nodes.ground_findings_quotes",
        AsyncMock(side_effect=RuntimeError("429 rate limit exceeded")),
    )
    state = {
        "tenant_id": "demo",
        "ingest_result": _ingest_result(),
        "findings": [_finding()],
        "indexed_policies": [],
    }
    with pytest.raises(RuntimeError, match="429"):
        await grounding_node(state, MagicMock())
