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
from review_agent.schemas.obligation_compare import BatchObligationCompareLLMResult, ObligationCompareItem
from review_agent.schemas.routing_plan import CatalogMatchResult, ObligationRoutingPlan
from review_agent.services.obligation_compare_llm import compare_obligations_batch, ipc_item_from_evidence
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
        "tenant_id": "e2e-demo",
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
        obligation_routing_tenant_allowlist="e2e-demo",
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
        "tenant_id": "e2e-demo",
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
            obligation_routing_tenant_allowlist="e2e-demo",
        )
        with patch(
            "review_agent.graph.obligation_compare_nodes.compare_obligations_batch",
            new_callable=AsyncMock,
        ) as mock_compare:
            out = await obligation_compare_node(state, AsyncMock())
            mock_compare.assert_not_called()
    assert out["obligation_findings"]
    assert out["compliance_stats"]["obligation_ipc_findings"] == 1


@pytest.mark.asyncio
async def test_compare_batch_omitted_obligation_gets_inconclusive():
    ob_a = ContractObligation(obligation_id="2.3-o0", section_id="2.3", text="Notify within 8 hours.")
    ob_b = ContractObligation(obligation_id="2.3-o1", section_id="2.3", text="Maintain audit logs.")
    evidence = {
        ob_a.obligation_id: EvidenceSufficiencyResult(
            obligation_id=ob_a.obligation_id,
            decision="compare",
            reason="evidence_sufficient",
        ),
        ob_b.obligation_id: EvidenceSufficiencyResult(
            obligation_id=ob_b.obligation_id,
            decision="compare",
            reason="evidence_sufficient",
        ),
    }
    batch_result = BatchObligationCompareLLMResult(
        items=[
            ObligationCompareItem(
                obligation_id=ob_a.obligation_id,
                section_id=ob_a.section_id,
                status=ComplianceStatus.COMPLIANT,
                rationale="Aligned with policy requirements for notification.",
            )
        ]
    )
    with patch(
        "review_agent.services.obligation_compare_llm._invoke_compare_batch",
        new_callable=AsyncMock,
        return_value=batch_result,
    ):
        items, warnings, stats = await compare_obligations_batch(
            [ob_a, ob_b],
            evidence,
            {ob_a.obligation_id: [], ob_b.obligation_id: []},
            settings=ReviewSettings(obligation_compare_batch_size=2),
        )
    assert stats["obligation_compare_omitted"] == 1
    assert len(items) == 2
    omitted = next(i for i in items if i.obligation_id == ob_b.obligation_id)
    assert omitted.status == ComplianceStatus.INCONCLUSIVE
    assert any("omitted" in w for w in warnings)


def test_obligation_item_null_policy_section_id_coerces():
    item = ObligationCompareItem.model_validate(
        {
            "obligation_id": "2.3-o0",
            "section_id": "2.3",
            "status": "COMPLIANT",
            "rationale": "Aligned with policy requirements.",
            "policy_section_id": None,
            "policy_document_id": None,
        }
    )
    assert item.policy_section_id == ""
    assert item.policy_document_id == ""


def test_backfill_obligation_policy_ids_from_single_hit():
    from uuid import uuid4

    from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk, RetrievalHit
    from review_agent.services.obligation_compare_llm import _backfill_obligation_policy_ids

    doc_id = str(uuid4())
    chunk = IndexedChunk(
        chunk_id="c1",
        document_id=doc_id,
        tenant_id="t1",
        kind=DocumentKind.POLICY,
        chunk_role=ChunkRole.PARENT,
        section_id="sec-1",
        section_path="sec-1",
        title="Security",
        text="policy body",
    )
    hit = RetrievalHit(parent_chunk=chunk, matched_child_ids=[], score=0.9)
    item = ObligationCompareItem(
        obligation_id="2.3-o0",
        section_id="2.3",
        status=ComplianceStatus.COMPLIANT,
        rationale="Aligned with policy requirements.",
    )
    filled, backfilled = _backfill_obligation_policy_ids(item, [hit])
    assert backfilled is True
    assert filled.policy_document_id == doc_id
    assert filled.policy_section_id == "sec-1"


@pytest.mark.asyncio
async def test_batch_failure_single_retry_recovers():
    ob_a = ContractObligation(obligation_id="2.3-o0", section_id="2.3", text="Notify within 8 hours.")
    ob_b = ContractObligation(obligation_id="2.3-o1", section_id="2.3", text="Maintain audit logs.")
    evidence = {
        ob_a.obligation_id: EvidenceSufficiencyResult(
            obligation_id=ob_a.obligation_id,
            decision="compare",
            reason="evidence_sufficient",
        ),
        ob_b.obligation_id: EvidenceSufficiencyResult(
            obligation_id=ob_b.obligation_id,
            decision="compare",
            reason="evidence_sufficient",
        ),
    }

    async def _invoke(batch, hits, **kwargs):
        if len(batch) > 1:
            raise ValueError("validation error for BatchObligationCompareLLMResult")
        return BatchObligationCompareLLMResult(
            items=[
                ObligationCompareItem(
                    obligation_id=batch[0].obligation_id,
                    section_id=batch[0].section_id,
                    status=ComplianceStatus.COMPLIANT,
                    rationale="Aligned with policy requirements for this obligation.",
                )
            ]
        )

    with patch(
        "review_agent.services.obligation_compare_llm._invoke_compare_batch",
        side_effect=_invoke,
    ):
        items, _warnings, stats = await compare_obligations_batch(
            [ob_a, ob_b],
            evidence,
            {ob_a.obligation_id: [], ob_b.obligation_id: []},
            settings=ReviewSettings(
                obligation_compare_batch_size=2,
                compare_batch_retry_single=True,
                equivalence_guard_enabled=False,
            ),
        )
    assert stats["obligation_compare_single_retries"] == 1
    assert stats["obligation_compare_llm_batches_failed"] == 0
    assert stats["obligation_compare_single_recovered"] == 2
    assert len(items) == 2
    assert all(i.status != ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT for i in items)
