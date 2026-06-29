"""Tests for RC-07 recovery gap promotion."""

from __future__ import annotations

from uuid import uuid4

from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk, RetrievalHit
from document_core.schemas.compliance import ComplianceFinding, ComplianceStatus, Severity
from review_agent.schemas.section_compare import SectionCompareItem
from review_agent.schemas.section_retrieval import SectionRetrievalBundle
from review_agent.services.recovery_gap_candidates import promote_recovery_compare_omitted_gaps


def _hit(doc_id: str | None = None) -> RetrievalHit:
    chunk = IndexedChunk(
        chunk_id="c1",
        document_id=uuid4() if doc_id is None else doc_id,
        tenant_id="t1",
        kind=DocumentKind.POLICY,
        chunk_role=ChunkRole.PARENT,
        section_id="1",
        section_path="1",
        title="Policy",
        text="Indemnification clause text.",
    )
    return RetrievalHit(parent_chunk=chunk, matched_child_ids=[], score=0.8)


def _bundle(section_id: str, *, with_hits: bool = True) -> SectionRetrievalBundle:
    return SectionRetrievalBundle(
        section_id=section_id,
        policy_hits=[_hit()] if with_hits else [],
        categories=["privacy"],
    )


def _obligation_ipc(section_id: str) -> ComplianceFinding:
    return ComplianceFinding(
        finding_id="f-obl",
        dimension_id=f"{section_id}:obl",
        dimension_label="obligation",
        status=ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
        contract_section_id=section_id,
        metadata={"source": "obligation_ipc"},
    )


def test_promotes_hit_backed_obligation_ipc_section():
    bundles = {"15": _bundle("15")}
    compare_items: list[SectionCompareItem] = []
    omitted, gaps, promoted = promote_recovery_compare_omitted_gaps(
        compare_items=compare_items,
        bundles=bundles,
        obligation_findings=[_obligation_ipc("15")],
        section_findings=[],
        compare_omitted_gap_ids=[],
        gap_section_ids=[],
    )
    assert promoted == ["15"]
    assert omitted == ["15"]
    assert gaps == ["15"]


def test_skips_when_playbook_compare_non_compliant_exists():
    bundles = {"15": _bundle("15")}
    compare_items = [
        SectionCompareItem(
            section_id="15",
            dimension_label="Indemnity",
            status=ComplianceStatus.NON_COMPLIANT,
            severity=Severity.CRITICAL,
            contract_quote="Cap liability.",
            policy_quote="Unlimited indemnity.",
            rationale="Conflict.",
            confidence=0.9,
        )
    ]
    omitted, gaps, promoted = promote_recovery_compare_omitted_gaps(
        compare_items=compare_items,
        bundles=bundles,
        obligation_findings=[_obligation_ipc("15")],
        section_findings=[],
        compare_omitted_gap_ids=[],
        gap_section_ids=[],
    )
    assert promoted == []
    assert omitted == []
    assert gaps == []


def test_idempotent_when_already_listed():
    bundles = {"19": _bundle("19")}
    omitted, gaps, promoted = promote_recovery_compare_omitted_gaps(
        compare_items=[],
        bundles=bundles,
        obligation_findings=[_obligation_ipc("19")],
        section_findings=[],
        compare_omitted_gap_ids=["19"],
        gap_section_ids=["19"],
    )
    assert promoted == []
    assert omitted == ["19"]
