"""Tests for CA-1 compare failure classification."""

from unittest.mock import patch
from uuid import uuid4

import pytest
from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk, RetrievalHit
from document_core.schemas.compliance import ComplianceStatus

from review_agent.config import ReviewSettings
from review_agent.schemas.evidence_sufficiency import EvidenceSufficiencyResult
from review_agent.schemas.obligation import ContractObligation
from review_agent.services.compare_failure_status import classify_compare_failure
from review_agent.services.obligation_compare_llm import compare_obligations_batch
from review_agent.services.section_compare_llm import _failure_items, _format_sections_block


def test_classify_429_hot_posture_ipc():
    status = classify_compare_failure(
        "HTTP 429 rate limit exceeded",
        has_policy_evidence=True,
        obligation_section_cutover_mode="skip",
        llm_review_posture="hot",
    )
    assert status == ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT


def test_classify_429_inconclusive():
    status = classify_compare_failure(
        "HTTP 429 rate limit exceeded",
        has_policy_evidence=True,
        obligation_section_cutover_mode="skip",
    )
    assert status == ComplianceStatus.INCONCLUSIVE


def test_classify_ipc_fallback_mode_429_is_ipc():
    status = classify_compare_failure(
        "HTTP 429 rate limit exceeded",
        has_policy_evidence=True,
        obligation_section_cutover_mode="ipc_fallback",
    )
    assert status == ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT


def test_classify_no_hits_ipc():
    status = classify_compare_failure("connection reset", has_policy_evidence=False)
    assert status == ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT


def test_classify_with_hits_transient_inconclusive():
    status = classify_compare_failure("validation error for batch", has_policy_evidence=True)
    assert status == ComplianceStatus.INCONCLUSIVE


def test_classify_rollback_flag_ipc():
    status = classify_compare_failure(
        "HTTP 429 rate limit exceeded",
        has_policy_evidence=True,
        transient_inconclusive=False,
    )
    assert status == ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT


def test_section_failure_items_transient():
    section = IndexedChunk(
        chunk_id="c1",
        document_id=uuid4(),
        tenant_id="demo",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id="s1",
        section_path="s1",
        title="Liability",
        text="Liability is capped.",
    )
    hit = RetrievalHit(
        parent_chunk=IndexedChunk(
            chunk_id="p1",
            document_id=uuid4(),
            tenant_id="demo",
            kind=DocumentKind.POLICY,
            chunk_role=ChunkRole.PARENT,
            section_id="p1",
            section_path="p1",
            title="Policy",
            text="Cap required.",
        ),
        score=1.0,
    )
    items = _failure_items(
        [section],
        reason="429 Too Many Requests",
        hits_by_section={"s1": [hit]},
    )
    assert len(items) == 1
    assert items[0].status == ComplianceStatus.INCONCLUSIVE


def test_section_failure_items_no_hits_ipc():
    section = IndexedChunk(
        chunk_id="c1",
        document_id=uuid4(),
        tenant_id="demo",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id="s1",
        section_path="s1",
        title="Liability",
        text="Liability is capped.",
    )
    items = _failure_items([section], reason="LLM unavailable", hits_by_section={"s1": []})
    assert items[0].status == ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT


def test_truncated_marker_in_block():
    section = IndexedChunk(
        chunk_id="c1",
        document_id=uuid4(),
        tenant_id="demo",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id="s1",
        section_path="s1",
        title="Long",
        text="word " * 500,
    )
    block, truncated_ids = _format_sections_block(
        [section],
        {"s1": []},
        max_section_chars=100,
    )
    assert "[truncated]" in block
    assert "s1" in truncated_ids


@pytest.mark.asyncio
async def test_obligation_batch_fail_transient():
    ob = ContractObligation(obligation_id="2.3-o0", section_id="2.3", text="Notify within 8 hours.")
    evidence = {
        ob.obligation_id: EvidenceSufficiencyResult(
            obligation_id=ob.obligation_id,
            decision="compare",
            reason="evidence_sufficient",
        ),
    }
    hit = RetrievalHit(
        parent_chunk=IndexedChunk(
            chunk_id="p1",
            document_id=uuid4(),
            tenant_id="demo",
            kind=DocumentKind.POLICY,
            chunk_role=ChunkRole.PARENT,
            section_id="p1",
            section_path="p1",
            title="Policy",
            text="Notify within 24 hours.",
        ),
        score=1.0,
    )

    async def _fail(*_args, **_kwargs):
        raise RuntimeError("429 Too Many Requests")

    with patch(
        "review_agent.services.obligation_compare_llm._compare_obligation_batch_with_retry",
        side_effect=_fail,
    ):
        items, _warnings, stats = await compare_obligations_batch(
            [ob],
            evidence,
            {ob.obligation_id: [hit]},
            settings=ReviewSettings(
                obligation_compare_batch_size=1,
                compare_batch_retry_single=False,
                equivalence_guard_enabled=False,
            ),
        )
    assert len(items) == 1
    assert items[0].status == ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT
    assert stats["obligation_compare_transient_failure_count"] == 0
