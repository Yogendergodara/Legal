"""Tests for obligation compare and merge (Phase R6)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from document_core.schemas.compliance import ComplianceStatus
from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk, RetrievalHit
from review_agent.config import ReviewSettings
from review_agent.graph.obligation_compare_nodes import obligation_compare_node
from review_agent.graph.section_compare_nodes import _sections_for_legacy_compare
from review_agent.schemas.evidence_sufficiency import EvidenceSufficiencyResult
from review_agent.schemas.obligation import ContractObligation
from review_agent.schemas.obligation_compare import ObligationCompareItem
from review_agent.schemas.routing_plan import CatalogMatchResult, ObligationRoutingPlan
from review_agent.services.obligation_compare_llm import ipc_item_from_evidence
from review_agent.services.obligation_merge import obligation_items_to_findings


def _section(section_id: str) -> IndexedChunk:
    return IndexedChunk(
        chunk_id=f"{section_id}:p",
        document_id=uuid4(),
        tenant_id="t1",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id=section_id,
        section_path=section_id,
        title=section_id,
        text="section body",
    )


def test_obligation_ipc_no_llm():
    ob = ContractObligation(
        obligation_id="10.1-o0",
        section_id="10.1",
        text="Governed by Wyoming law.",
        is_boilerplate=True,
    )
    evidence = EvidenceSufficiencyResult(
        obligation_id=ob.obligation_id,
        decision="ipc",
        reason="routing_or_skip",
    )
    item = ipc_item_from_evidence(
        ob,
        evidence,
        plan=ObligationRoutingPlan(
            obligation_id=ob.obligation_id,
            routing_source="skipped_boilerplate",
            confidence=0.0,
        ),
        match=CatalogMatchResult(obligation_id=ob.obligation_id, route_decision="ipc"),
    )
    assert item.status == ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT


def test_obligation_merge_finding_metadata():
    item = ObligationCompareItem(
        obligation_id="2.3-o0",
        section_id="2.3",
        status=ComplianceStatus.COMPLIANT,
        rationale="Aligned with security practices policy requirements.",
        policy_document_id=str(uuid4()),
    )
    audit = {
        "2.3-o0": {
            "obligation_id": "2.3-o0",
            "routing_source": "registry_alias",
            "routing_confidence": 1.0,
        }
    }
    findings = obligation_items_to_findings([item], routing_audit_by_obligation=audit)
    assert len(findings) == 1
    assert findings[0].metadata["obligation_id"] == "2.3-o0"
    assert findings[0].metadata["routing_audit"]["routing_source"] == "registry_alias"


def test_section_cutover_skips_obligation_sections():
    sections = [_section("2.3"), _section("10.1")]
    state = {
        "obligations": [
            ContractObligation(obligation_id="2.3-o0", section_id="2.3", text="security").model_dump(
                mode="json"
            )
        ]
    }
    settings = ReviewSettings(
        obligation_routing_enabled=True,
        obligation_compare_enabled=True,
        obligation_section_cutover_mode="skip",
    )
    filtered = _sections_for_legacy_compare(sections, state, settings)
    assert [s.section_id for s in filtered] == ["10.1"]


@pytest.mark.asyncio
async def test_graph_node_flag_off():
    with patch("review_agent.graph.obligation_compare_nodes.get_settings") as mock_settings:
        mock_settings.return_value = ReviewSettings(obligation_routing_enabled=False)
        out = await obligation_compare_node({"tenant_id": "t"}, AsyncMock())
    assert out == {}


@pytest.mark.asyncio
async def test_obligation_compare_node_ipc_only():
    ob = ContractObligation(obligation_id="10.5-o0", section_id="10.5", text="Notices in writing.")
    state = {
        "tenant_id": "t1",
        "obligations": [ob.model_dump(mode="json")],
        "obligation_evidence_by_id": {
            "10.5-o0": EvidenceSufficiencyResult(
                obligation_id="10.5-o0",
                decision="ipc",
                reason="routing_or_skip",
            ).model_dump(mode="json")
        },
        "obligation_routing_by_id": {
            "10.5-o0": ObligationRoutingPlan(
                obligation_id="10.5-o0",
                routing_source="skipped_boilerplate",
            ).model_dump(mode="json")
        },
        "obligation_catalog_match_by_id": {
            "10.5-o0": CatalogMatchResult(
                obligation_id="10.5-o0",
                route_decision="ipc",
            ).model_dump(mode="json")
        },
        "obligation_retrieval_by_id": {},
        "indexed_policies": [],
        "compliance_stats": {},
    }
    with patch("review_agent.graph.obligation_compare_nodes.get_settings") as mock_settings:
        mock_settings.return_value = ReviewSettings(
            obligation_routing_enabled=True,
            obligation_compare_enabled=True,
        )
        with patch(
            "review_agent.graph.obligation_compare_nodes.compare_obligations_batch",
            new_callable=AsyncMock,
        ) as mock_compare:
            out = await obligation_compare_node(state, AsyncMock())
            mock_compare.assert_not_called()
    assert out["obligation_findings"]
    assert out["compliance_stats"]["obligation_ipc_findings"] == 1
