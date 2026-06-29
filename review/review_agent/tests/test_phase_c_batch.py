"""Phase C batch consolidation tests."""

from __future__ import annotations

from uuid import uuid4

import pytest
from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk, RetrievalHit
from document_core.schemas.compliance import ComplianceFinding, ComplianceStatus

from review_agent.config import ReviewSettings
from review_agent.schemas.section_retrieval import SectionRetrievalBundle
from review_agent.services.final_verify_llm import run_final_gap_verify
from review_agent.services.quote_repair_llm import QuoteRepairJob, repair_quotes_batch
from review_agent.schemas.quote_repair import QuoteRepairResult


def _section(section_id: str) -> IndexedChunk:
    return IndexedChunk(
        chunk_id=f"c-{section_id}",
        document_id=uuid4(),
        tenant_id="demo",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id=section_id,
        section_path=section_id,
        title=f"Section {section_id}",
        text=f"Contract text for {section_id} with enough substance for review.",
    )


@pytest.mark.asyncio
async def test_gap_re_retrieve_compare_batched(monkeypatch):
    compare_calls: list[int] = []

    async def _fake_multi_retrieve(*_args, section, **_kwargs):
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
                text="Policy requirement text.",
            ),
            score=0.9,
        )
        return SectionRetrievalBundle(
            section_id=section.section_id,
            categories=["indemnity"],
            policy_hits=[hit],
            retrieval_meta={},
        )

    async def _fake_compare_gated(sections, *_args, **_kwargs):
        compare_calls.append(len(sections))
        return [], [], []

    monkeypatch.setattr(
        "review_agent.services.final_verify_llm.multi_retrieve_for_section",
        _fake_multi_retrieve,
    )
    monkeypatch.setattr(
        "review_agent.services.final_verify_llm._compare_sections_gated",
        _fake_compare_gated,
    )
    monkeypatch.setattr(
        "review_agent.services.final_verify_llm.verify_gap_sections_llm",
        lambda *_a, **_k: ([], [], 0),
    )

    section_ids = [f"s-gap-{i}" for i in range(5)]
    sections_by_id = {sid: _section(sid) for sid in section_ids}
    bundles = {
        sid: SectionRetrievalBundle(section_id=sid, categories=[], policy_hits=[])
        for sid in section_ids
    }
    existing = [
        ComplianceFinding(
            finding_id=f"f-{sid}",
            dimension_id=f"{sid}:no_policy",
            dimension_label="no policy",
            status=ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
            contract_section_id=sid,
            rationale="No policy retrieved initially.",
            metadata={"gap_type": "no_policy"},
        )
        for sid in section_ids
    ]

    _new, _warnings, stats, _superseded = await run_final_gap_verify(
        client=object(),
        tenant_id="demo",
        sections_by_id=sections_by_id,
        bundles=bundles,
        gap_section_ids=section_ids,
        existing_findings=existing,
        contract_type="msa",
        policy_type=None,
        settings=ReviewSettings(section_compare_batch_size=8),
    )
    assert stats["re_retrieved"] == 5
    assert stats["resolved_with_policy"] == 5
    assert stats["gap_recompare_batches"] == 1
    assert compare_calls == [5]


@pytest.mark.asyncio
async def test_repair_quotes_batch_single_llm_call(monkeypatch):
    calls = {"n": 0}

    async def _fake_invoke(_model, schema, *, system, user):
        calls["n"] += 1
        from review_agent.schemas.quote_repair import BatchQuoteRepairLLMResult, QuoteRepairBatchItem

        return BatchQuoteRepairLLMResult(
            items=[
                QuoteRepairBatchItem(
                    repair_id=f"r{i}",
                    section_id=f"s{i}",
                    repaired_quote="exact span",
                    repair_notes="ok",
                )
                for i in range(3)
            ]
        )

    monkeypatch.setattr(
        "review_agent.services.quote_repair_llm.invoke_structured",
        _fake_invoke,
    )
    monkeypatch.setattr(
        "review_agent.services.quote_repair_llm.get_review_model",
        lambda **_: object(),
    )

    jobs = [
        QuoteRepairJob(
            repair_id=f"r{i}",
            section_id=f"s{i}",
            source_text=f"exact span in section {i}",
            candidate_quote="bad",
        )
        for i in range(3)
    ]
    out = await repair_quotes_batch(
        jobs,
        settings=ReviewSettings(quote_repair_batch_enabled=True, quote_repair_batch_size=6),
    )
    assert calls["n"] == 1
    assert len(out) == 3
    assert out["r0"].repaired_quote == "exact span"
