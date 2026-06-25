"""Tests for final gap verify pass."""

from uuid import uuid4

import pytest
from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk, RetrievalHit
from document_core.schemas.compliance import ComplianceFinding, ComplianceStatus, Severity

from review_agent.schemas.section_compare import FinalGapVerifyItem
from review_agent.schemas.section_retrieval import SectionRetrievalBundle
from review_agent.services.final_verify_llm import run_final_gap_verify


def _section(section_id: str, text: str | None = None) -> IndexedChunk:
    return IndexedChunk(
        chunk_id="c1",
        document_id=uuid4(),
        tenant_id="demo",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id=section_id,
        section_path=section_id,
        title=section_id,
        text=text or "Indemnification clause requires vendor to hold harmless customer.",
    )


@pytest.mark.asyncio
async def test_gap_verify_skipped_when_disabled(monkeypatch):
    from review_agent.config import ReviewSettings, get_settings

    monkeypatch.setenv("FINAL_GAP_VERIFY_ENABLED", "false")
    get_settings.cache_clear()

    findings, warnings, stats, superseded = await run_final_gap_verify(
        client=object(),
        tenant_id="demo",
        sections_by_id={"s1": _section("s1")},
        bundles={},
        gap_section_ids=["s1"],
        existing_findings=[],
        contract_type="msa",
        policy_type=None,
        settings=ReviewSettings(final_gap_verify_enabled=False),
    )
    assert not findings
    assert not superseded
    assert stats.get("skipped") is True


@pytest.mark.asyncio
async def test_final_verify_recompares_compare_omitted(monkeypatch):
    section = _section("s-omit", "Vendor shall indemnify customer for all claims.")
    policy_hit = RetrievalHit(
        parent_chunk=IndexedChunk(
            chunk_id="p1",
            document_id=uuid4(),
            tenant_id="demo",
            kind=DocumentKind.POLICY,
            chunk_role=ChunkRole.PARENT,
            section_id="5",
            section_path="5",
            title="Indemnity",
            text="Vendor must indemnify customer for third-party claims.",
            metadata={"categories": ["indemnity"]},
        ),
        score=0.8,
    )
    bundle = SectionRetrievalBundle(
        section_id="s-omit",
        categories=["indemnity"],
        policy_hits=[policy_hit],
    )

    async def _fake_compare(*_args, **_kwargs):
        from review_agent.schemas.section_compare import SectionCompareItem

        return (
            [
                SectionCompareItem(
                    section_id="s-omit",
                    policy_document_id=str(policy_hit.parent_chunk.document_id),
                    policy_section_id="5",
                    dimension_label="Indemnification",
                    status=ComplianceStatus.NON_COMPLIANT,
                    severity=Severity.CRITICAL,
                    rationale="Contract indemnity scope is narrower than policy requires.",
                    confidence=0.85,
                )
            ],
            [],
        )

    retrieve_called = False

    async def _fake_multi_retrieve(*_args, **_kwargs):
        nonlocal retrieve_called
        retrieve_called = True
        return bundle

    monkeypatch.setattr(
        "review_agent.services.final_verify_llm.compare_section_batch",
        _fake_compare,
    )
    monkeypatch.setattr(
        "review_agent.services.final_verify_llm.multi_retrieve_for_section",
        _fake_multi_retrieve,
    )

    gap_finding = ComplianceFinding(
        finding_id="f-omit",
        dimension_id="s-omit:compare_omitted",
        dimension_label="compare omitted",
        status=ComplianceStatus.INCONCLUSIVE,
        contract_section_id="s-omit",
        rationale="Compare omitted initially.",
        metadata={"gap_type": "compare_omitted", "review_outcome": "pipeline_incomplete"},
    )

    new_findings, _warnings, stats, superseded = await run_final_gap_verify(
        client=object(),
        tenant_id="demo",
        sections_by_id={"s-omit": section},
        bundles={"s-omit": bundle},
        gap_section_ids=["s-omit"],
        compare_omitted_gap_ids=["s-omit"],
        no_policy_gap_ids=[],
        existing_findings=[gap_finding],
        contract_type="msa",
        policy_type=None,
    )
    assert not retrieve_called
    assert stats["compare_omitted_recovered"] == 1
    assert len(new_findings) == 1
    assert new_findings[0].status == ComplianceStatus.NON_COMPLIANT
    assert "f-omit" in superseded


@pytest.mark.asyncio
async def test_gap_verify_re_retrieve_and_compare(monkeypatch):
    section = _section("s-gap")
    policy_hit = RetrievalHit(
        parent_chunk=IndexedChunk(
            chunk_id="p1",
            document_id=uuid4(),
            tenant_id="demo",
            kind=DocumentKind.POLICY,
            chunk_role=ChunkRole.PARENT,
            section_id="5",
            section_path="5",
            title="Indemnity",
            text="Vendor must indemnify customer for third-party claims.",
            metadata={"categories": ["indemnity"]},
        ),
        score=0.8,
    )

    async def _fake_multi_retrieve(*_args, **_kwargs):
        return SectionRetrievalBundle(
            section_id="s-gap",
            categories=["indemnity"],
            policy_hits=[policy_hit],
            retrieval_meta={"dense_count": 1},
        )

    async def _fake_compare(*_args, **_kwargs):
        from review_agent.schemas.section_compare import SectionCompareItem

        return (
            [
                SectionCompareItem(
                    section_id="s-gap",
                    policy_document_id=str(policy_hit.parent_chunk.document_id),
                    policy_section_id="5",
                    dimension_label="Indemnification",
                    status=ComplianceStatus.NON_COMPLIANT,
                    severity=Severity.CRITICAL,
                    rationale="Contract indemnity scope is narrower than policy requires.",
                    confidence=0.8,
                )
            ],
            [],
        )

    gap_llm_called = False

    async def _fake_gap_llm(*_args, **_kwargs):
        nonlocal gap_llm_called
        gap_llm_called = True
        return [], [], 0

    monkeypatch.setattr(
        "review_agent.services.final_verify_llm.multi_retrieve_for_section",
        _fake_multi_retrieve,
    )
    monkeypatch.setattr(
        "review_agent.services.final_verify_llm.compare_section_batch",
        _fake_compare,
    )
    monkeypatch.setattr(
        "review_agent.services.final_verify_llm.verify_gap_sections_llm",
        _fake_gap_llm,
    )

    new_findings, _warnings, stats, _superseded = await run_final_gap_verify(
        client=object(),
        tenant_id="demo",
        sections_by_id={"s-gap": section},
        bundles={"s-gap": SectionRetrievalBundle(section_id="s-gap", categories=[], policy_hits=[])},
        gap_section_ids=["s-gap"],
        existing_findings=[
            ComplianceFinding(
                finding_id="f1",
                dimension_id="s-gap:no_policy",
                dimension_label="no policy",
                status=ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
                contract_section_id="s-gap",
                rationale="No policy retrieved initially.",
                metadata={"gap_type": "no_policy"},
            )
        ],
        contract_type="msa",
        policy_type=None,
    )
    assert stats["resolved_with_policy"] == 1
    assert len(new_findings) == 1
    assert new_findings[0].status == ComplianceStatus.NON_COMPLIANT
    assert not gap_llm_called


@pytest.mark.asyncio
async def test_re_retrieve_off_topic_blocked_by_coverage_gate(monkeypatch):
    from review_agent.config import ReviewSettings

    section = IndexedChunk(
        chunk_id="c-gl",
        document_id=uuid4(),
        tenant_id="demo",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id="10.1",
        section_path="10.1",
        title="Governing Law",
        text="This Agreement shall be governed by Wyoming law.",
    )
    ir_hit = RetrievalHit(
        parent_chunk=IndexedChunk(
            chunk_id="p-ir",
            document_id=uuid4(),
            tenant_id="demo",
            kind=DocumentKind.POLICY,
            chunk_role=ChunkRole.PARENT,
            section_id="10",
            section_path="10",
            title="Incident Response Plan",
            text="Prepare incident report within 8 hours.",
            metadata={"categories": ["incident_reporting"]},
        ),
        score=0.9,
    )

    compare_called = False

    async def _fake_multi_retrieve(*_args, **_kwargs):
        return SectionRetrievalBundle(
            section_id="10.1",
            categories=["governing_law"],
            policy_hits=[ir_hit],
            retrieval_meta={"dense_count": 1},
        )

    async def _fake_compare(*_args, **_kwargs):
        nonlocal compare_called
        compare_called = True
        raise AssertionError("compare should not run for off-topic re-retrieve")

    monkeypatch.setattr(
        "review_agent.services.final_verify_llm.multi_retrieve_for_section",
        _fake_multi_retrieve,
    )
    monkeypatch.setattr(
        "review_agent.services.final_verify_llm.compare_section_batch",
        _fake_compare,
    )

    new_findings, _warnings, stats, _superseded = await run_final_gap_verify(
        client=object(),
        tenant_id="demo",
        sections_by_id={"10.1": section},
        bundles={"10.1": SectionRetrievalBundle(section_id="10.1", categories=[], policy_hits=[])},
        gap_section_ids=["10.1"],
        existing_findings=[
            ComplianceFinding(
                finding_id="f1",
                dimension_id="10.1:no_policy",
                dimension_label="Governing Law",
                status=ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
                contract_section_id="10.1",
                rationale="No policy retrieved initially.",
                metadata={"gap_type": "no_policy"},
            )
        ],
        contract_type="nda",
        policy_type=None,
        settings=ReviewSettings(policy_coverage_enabled=True),
    )
    assert stats["resolved_with_policy"] == 1
    assert not compare_called
    assert len(new_findings) == 1
    assert new_findings[0].status == ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT


@pytest.mark.asyncio
async def test_gap_llm_runs_when_no_hits_after_retrieve(monkeypatch):
    section = _section("s-nohit")
    contract_text = section.text or ""

    async def _fake_multi_retrieve(*_args, **_kwargs):
        return SectionRetrievalBundle(
            section_id="s-nohit",
            categories=["indemnity"],
            policy_hits=[],
            retrieval_meta={},
        )

    async def _fake_gap_llm(sections, bundles, *, contract_type, settings):
        return (
            [
                ComplianceFinding(
                    finding_id="gap-1",
                    dimension_id="s-nohit:final_gap",
                    dimension_label="s-nohit",
                    status=ComplianceStatus.NON_COMPLIANT,
                    severity=Severity.CRITICAL,
                    contract_quote=contract_text[:40],
                    contract_section_id="s-nohit",
                    rationale="Risk visible without matching playbook.",
                    metadata={"final_verify": "gap_llm", "gap_type": "no_policy"},
                )
            ],
            [],
            0,
        )

    monkeypatch.setattr(
        "review_agent.services.final_verify_llm.multi_retrieve_for_section",
        _fake_multi_retrieve,
    )
    monkeypatch.setattr(
        "review_agent.services.final_verify_llm.verify_gap_sections_llm",
        _fake_gap_llm,
    )

    new_findings, _warnings, stats, superseded = await run_final_gap_verify(
        client=object(),
        tenant_id="demo",
        sections_by_id={"s-nohit": section},
        bundles={"s-nohit": SectionRetrievalBundle(section_id="s-nohit", categories=[], policy_hits=[])},
        gap_section_ids=["s-nohit"],
        existing_findings=[
            ComplianceFinding(
                finding_id="f-gap",
                dimension_id="s-nohit:no_policy",
                dimension_label="no policy",
                status=ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
                contract_section_id="s-nohit",
                rationale="No policy retrieved initially.",
                metadata={"gap_type": "no_policy"},
            )
        ],
        contract_type="msa",
        policy_type=None,
    )
    assert stats["gap_llm_sections"] == 1
    assert len(new_findings) == 1
    assert new_findings[0].metadata.get("final_verify") == "gap_llm"
    assert "f-gap" in superseded


@pytest.mark.asyncio
async def test_unclear_triggers_recompare(monkeypatch):
    section = _section("s-unclear")
    policy_hit = RetrievalHit(
        parent_chunk=IndexedChunk(
            chunk_id="p1",
            document_id=uuid4(),
            tenant_id="demo",
            kind=DocumentKind.POLICY,
            chunk_role=ChunkRole.PARENT,
            section_id="5",
            section_path="5",
            title="Indemnity",
            text="Vendor must indemnify customer.",
        ),
        score=0.8,
    )
    bundle = SectionRetrievalBundle(
        section_id="s-unclear",
        categories=["indemnity"],
        policy_hits=[policy_hit],
    )

    async def _fake_compare(*_args, **_kwargs):
        from review_agent.schemas.section_compare import SectionCompareItem

        return (
            [
                SectionCompareItem(
                    section_id="s-unclear",
                    policy_document_id=str(policy_hit.parent_chunk.document_id),
                    policy_section_id="5",
                    dimension_label="Indemnification",
                    status=ComplianceStatus.COMPLIANT,
                    severity=Severity.INFO,
                    rationale="Contract aligns with policy after second pass.",
                    confidence=0.9,
                )
            ],
            [],
        )

    monkeypatch.setattr(
        "review_agent.services.final_verify_llm.compare_section_batch",
        _fake_compare,
    )

    unclear_finding = ComplianceFinding(
        finding_id="f-unclear",
        dimension_id="s-unclear:5",
        dimension_label="Indemnification",
        status=ComplianceStatus.INCONCLUSIVE,
        contract_section_id="s-unclear",
        policy_quote="Vendor must indemnify customer.",
        rationale="Low confidence first pass.",
        metadata={
            "confidence": 0.3,
            "source": "playbook_compare",
            "needs_final_verify": True,
        },
    )

    new_findings, _warnings, stats, superseded = await run_final_gap_verify(
        client=object(),
        tenant_id="demo",
        sections_by_id={"s-unclear": section},
        bundles={"s-unclear": bundle},
        gap_section_ids=[],
        unclear_finding_ids=["f-unclear"],
        unclear_recompare_finding_ids=["f-unclear"],
        existing_findings=[unclear_finding],
        contract_type="msa",
        policy_type=None,
    )
    assert stats["unclear_recompared"] == 1
    assert len(new_findings) == 1
    assert new_findings[0].status == ComplianceStatus.COMPLIANT
    assert "f-unclear" in superseded


@pytest.mark.asyncio
async def test_conflict_triggers_recompare_with_context(monkeypatch):
    section = _section("s-conflict")
    policy_doc_id = uuid4()
    policy_hit = RetrievalHit(
        parent_chunk=IndexedChunk(
            chunk_id="p1",
            document_id=policy_doc_id,
            tenant_id="demo",
            kind=DocumentKind.POLICY,
            chunk_role=ChunkRole.PARENT,
            section_id="5",
            section_path="5",
            title="Indemnity",
            text="Vendor must indemnify customer.",
        ),
        score=0.8,
    )
    bundle = SectionRetrievalBundle(
        section_id="s-conflict",
        categories=["indemnity"],
        policy_hits=[policy_hit],
    )

    captured_context: list[str] = []

    async def _fake_compare(*_args, **kwargs):
        captured_context.append(kwargs.get("extra_user_context", ""))
        from review_agent.schemas.section_compare import SectionCompareItem

        return (
            [
                SectionCompareItem(
                    section_id="s-conflict",
                    policy_document_id=str(policy_doc_id),
                    policy_section_id="5",
                    dimension_label="Indemnification",
                    status=ComplianceStatus.NON_COMPLIANT,
                    severity=Severity.IMPORTANT,
                    rationale="Resolved conflict on second pass.",
                    confidence=0.85,
                )
            ],
            [],
        )

    monkeypatch.setattr(
        "review_agent.services.final_verify_llm.compare_section_batch",
        _fake_compare,
    )

    left = ComplianceFinding(
        finding_id="f-left",
        dimension_id="s-conflict:5",
        dimension_label="Indemnification",
        status=ComplianceStatus.COMPLIANT,
        contract_section_id="s-conflict",
        rationale="First assessor says compliant.",
    )
    right = ComplianceFinding(
        finding_id="f-right",
        dimension_id="s-conflict:5b",
        dimension_label="Indemnification",
        status=ComplianceStatus.NON_COMPLIANT,
        contract_section_id="s-conflict",
        rationale="Second assessor says non-compliant.",
    )

    new_findings, _warnings, stats, superseded = await run_final_gap_verify(
        client=object(),
        tenant_id="demo",
        sections_by_id={"s-conflict": section},
        bundles={"s-conflict": bundle},
        gap_section_ids=[],
        conflict_pairs=[("f-left", "f-right")],
        existing_findings=[left, right],
        contract_type="msa",
        policy_type=None,
    )
    assert stats["conflicts_recompared"] == 1
    assert len(new_findings) == 1
    assert "f-left" in superseded and "f-right" in superseded
    assert captured_context
    assert "COMPLIANT" in captured_context[0]
    assert "NON_COMPLIANT" in captured_context[0]


@pytest.mark.asyncio
async def test_unclear_recompare_capped_at_max_sections(monkeypatch):
    from review_agent.config import ReviewSettings

    policy_doc_id = uuid4()
    policy_hit = RetrievalHit(
        parent_chunk=IndexedChunk(
            chunk_id="p1",
            document_id=policy_doc_id,
            tenant_id="demo",
            kind=DocumentKind.POLICY,
            chunk_role=ChunkRole.PARENT,
            section_id="5",
            section_path="5",
            title="Policy",
            text="Policy requirement text.",
        ),
        score=0.8,
    )
    compare_calls: list[int] = []

    async def _fake_compare(sections, *_args, **_kwargs):
        compare_calls.append(len(sections))
        from review_agent.schemas.section_compare import SectionCompareItem

        items = [
            SectionCompareItem(
                section_id=s.section_id,
                policy_document_id=str(policy_doc_id),
                policy_section_id="5",
                dimension_label="Test",
                status=ComplianceStatus.COMPLIANT,
                severity=Severity.INFO,
                rationale="Recompared.",
                confidence=0.9,
            )
            for s in sections
        ]
        return items, []

    monkeypatch.setattr(
        "review_agent.services.final_verify_llm.compare_section_batch",
        _fake_compare,
    )

    sections_by_id = {}
    bundles = {}
    existing = []
    recompare_ids = []
    for i in range(6):
        sid = f"s{i}"
        sections_by_id[sid] = _section(sid)
        bundles[sid] = SectionRetrievalBundle(
            section_id=sid,
            categories=["general"],
            policy_hits=[policy_hit],
        )
        fid = f"f{i}"
        recompare_ids.append(fid)
        existing.append(
            ComplianceFinding(
                finding_id=fid,
                dimension_id=f"{sid}:5",
                dimension_label="Test",
                status=ComplianceStatus.INCONCLUSIVE,
                contract_section_id=sid,
                policy_quote="Policy requirement text.",
                rationale="Low confidence.",
                metadata={"confidence": 0.1 + i * 0.05, "source": "playbook_compare"},
            )
        )

    _new_findings, warnings, stats, _superseded = await run_final_gap_verify(
        client=object(),
        tenant_id="demo",
        sections_by_id=sections_by_id,
        bundles=bundles,
        gap_section_ids=[],
        unclear_finding_ids=recompare_ids,
        unclear_recompare_finding_ids=recompare_ids,
        existing_findings=existing,
        contract_type="msa",
        policy_type=None,
        settings=ReviewSettings(final_verify_unclear_recompare_max_sections=4),
    )
    assert stats["unclear_recompared"] == 4
    assert stats["unclear_recompare_capped"] == 2
    assert sum(compare_calls) == 4
    assert any("capped at 4" in w for w in warnings)


@pytest.mark.asyncio
async def test_unclear_recompare_skips_grounded_non_compliant(monkeypatch):
    from review_agent.config import ReviewSettings

    section = _section("s-grounded")
    policy_hit = RetrievalHit(
        parent_chunk=IndexedChunk(
            chunk_id="p1",
            document_id=uuid4(),
            tenant_id="demo",
            kind=DocumentKind.POLICY,
            chunk_role=ChunkRole.PARENT,
            section_id="5",
            section_path="5",
            title="Policy",
            text="Policy text.",
        ),
        score=0.8,
    )
    bundle = SectionRetrievalBundle(
        section_id="s-grounded",
        categories=["general"],
        policy_hits=[policy_hit],
    )
    compare_called = False

    async def _fake_compare(*_args, **_kwargs):
        nonlocal compare_called
        compare_called = True
        return [], []

    monkeypatch.setattr(
        "review_agent.services.final_verify_llm.compare_section_batch",
        _fake_compare,
    )

    grounded = ComplianceFinding(
        finding_id="f-nc",
        dimension_id="s-grounded:5",
        dimension_label="Test",
        status=ComplianceStatus.NON_COMPLIANT,
        contract_section_id="s-grounded",
        contract_quote="Bad clause.",
        policy_quote="Required clause.",
        rationale="Clear violation.",
        metadata={"source": "playbook_compare"},
        grounded=True,
    )
    unclear = ComplianceFinding(
        finding_id="f-unclear",
        dimension_id="s-grounded:5b",
        dimension_label="Test",
        status=ComplianceStatus.INCONCLUSIVE,
        contract_section_id="s-grounded",
        policy_quote="Required clause.",
        rationale="Low confidence.",
        metadata={"confidence": 0.2, "source": "playbook_compare"},
    )

    _new_findings, _warnings, stats, _superseded = await run_final_gap_verify(
        client=object(),
        tenant_id="demo",
        sections_by_id={"s-grounded": section},
        bundles={"s-grounded": bundle},
        gap_section_ids=[],
        unclear_finding_ids=["f-unclear"],
        unclear_recompare_finding_ids=["f-unclear"],
        existing_findings=[grounded, unclear],
        contract_type="msa",
        policy_type=None,
        settings=ReviewSettings(),
    )
    assert stats["unclear_recompared"] == 0
    assert stats["unclear_recompare_skipped"] == 1
    assert not compare_called


@pytest.mark.asyncio
async def test_unclear_recompare_disabled_by_config(monkeypatch):
    from review_agent.config import ReviewSettings

    section = _section("s-off")
    policy_hit = RetrievalHit(
        parent_chunk=IndexedChunk(
            chunk_id="p1",
            document_id=uuid4(),
            tenant_id="demo",
            kind=DocumentKind.POLICY,
            chunk_role=ChunkRole.PARENT,
            section_id="5",
            section_path="5",
            title="Policy",
            text="Policy text.",
        ),
        score=0.8,
    )
    bundle = SectionRetrievalBundle(
        section_id="s-off",
        categories=["general"],
        policy_hits=[policy_hit],
    )
    compare_called = False

    async def _fake_compare(*_args, **_kwargs):
        nonlocal compare_called
        compare_called = True
        return [], []

    monkeypatch.setattr(
        "review_agent.services.final_verify_llm.compare_section_batch",
        _fake_compare,
    )

    unclear = ComplianceFinding(
        finding_id="f-unclear",
        dimension_id="s-off:5",
        dimension_label="Test",
        status=ComplianceStatus.INCONCLUSIVE,
        contract_section_id="s-off",
        policy_quote="Policy text.",
        rationale="Low confidence.",
        metadata={"confidence": 0.2, "source": "playbook_compare"},
    )

    _new_findings, warnings, stats, _superseded = await run_final_gap_verify(
        client=object(),
        tenant_id="demo",
        sections_by_id={"s-off": section},
        bundles={"s-off": bundle},
        gap_section_ids=[],
        unclear_finding_ids=["f-unclear"],
        unclear_recompare_finding_ids=["f-unclear"],
        existing_findings=[unclear],
        contract_type="msa",
        policy_type=None,
        settings=ReviewSettings(final_verify_unclear_recompare_enabled=False),
    )
    assert stats["unclear_recompared"] == 0
    assert stats["unclear_recompare_skipped"] == 1
    assert not compare_called
    assert any("disabled by config" in w for w in warnings)


@pytest.mark.asyncio
async def test_verify_gap_sections_llm_normalizes_quotes(monkeypatch):
    from review_agent.services.final_verify_llm import verify_gap_sections_llm

    section = _section("s-quote", text="Vendor liability is unlimited.")
    bundle = SectionRetrievalBundle(section_id="s-quote", categories=["liability"], policy_hits=[])

    async def _fake_invoke(_model, _schema, *, system, user):
        from review_agent.schemas.section_compare import BatchFinalGapVerifyLLMResult

        return BatchFinalGapVerifyLLMResult(
            items=[
                FinalGapVerifyItem(
                    section_id="s-quote",
                    status=ComplianceStatus.NON_COMPLIANT,
                    severity=Severity.CRITICAL,
                    contract_quote="Vendor liability is unlimited.",
                    rationale="Unlimited liability is a material risk without playbook coverage.",
                )
            ]
        )

    monkeypatch.setattr(
        "review_agent.services.final_verify_llm.invoke_structured",
        _fake_invoke,
    )
    monkeypatch.setattr(
        "review_agent.services.final_verify_llm.get_review_model",
        lambda **_kwargs: object(),
    )

    from review_agent.config import ReviewSettings

    findings, warnings, failed = await verify_gap_sections_llm(
        [section],
        {"s-quote": bundle},
        contract_type="msa",
        settings=ReviewSettings(),
    )
    assert failed == 0
    assert len(findings) == 1
    assert findings[0].contract_quote == "Vendor liability is unlimited."
    assert findings[0].metadata.get("final_verify") == "gap_llm"
    assert not warnings
