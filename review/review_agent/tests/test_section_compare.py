"""Tests for section compare LLM (mocked)."""

from uuid import uuid4

import pytest
from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk, RetrievalHit
from document_core.schemas.compliance import ComplianceStatus, Severity

from review_agent.schemas.section_compare import BatchSectionCompareLLMResult, SectionCompareItem
from review_agent.services import section_compare_llm


def test_compare_prompt_includes_topic_mismatch_rules():
    system, _ = section_compare_llm._load_prompt_template()
    assert "Topic mismatch" in system
    assert "Legal notices" in system
    assert "applies to this section's topic" in system


def test_format_sections_includes_categories():
    section = _section("10.1", "Governing law text.")
    section = section.model_copy(update={"title": "Governing Law"})
    hit = _policy_hit("IR policy text", categories=["incident_reporting"])
    block, _ = section_compare_llm._format_sections_block(
        [section],
        {"10.1": [hit]},
        max_section_chars=8000,
        categories_by_section={"10.1": ["governing_law"]},
    )
    assert "- **Section categories:** governing_law" in block
    assert "- **Policy categories:** incident_reporting" in block


def _section(section_id: str, text: str) -> IndexedChunk:
    return IndexedChunk(
        chunk_id="c1",
        document_id=uuid4(),
        tenant_id="demo",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id=section_id,
        section_path=section_id,
        title=section_id,
        text=text,
    )


def _policy_hit(text: str, *, categories: list[str] | None = None) -> RetrievalHit:
    doc_id = uuid4()
    metadata = {"categories": categories} if categories else {}
    chunk = IndexedChunk(
        chunk_id="p1",
        document_id=doc_id,
        tenant_id="demo",
        kind=DocumentKind.POLICY,
        chunk_role=ChunkRole.PARENT,
        section_id="4",
        section_path="4",
        title="Policy",
        text=text,
        metadata=metadata,
    )
    return RetrievalHit(parent_chunk=chunk, score=1.0)


@pytest.mark.asyncio
async def test_compare_batch_returns_items(monkeypatch):
    contract_text = "Liability is unlimited for all claims."
    policy_text = "Liability shall not exceed twelve months fees."

    async def _fake_invoke(_model, _schema, *, system, user):
        assert contract_text in user
        assert policy_text in user
        return BatchSectionCompareLLMResult(
            items=[
                SectionCompareItem(
                    section_id="s1",
                    policy_section_id="4",
                    dimension_label="Liability",
                    status=ComplianceStatus.NON_COMPLIANT,
                    severity=Severity.CRITICAL,
                    contract_quote="Liability is unlimited for all claims.",
                    policy_quote="Liability shall not exceed twelve months fees.",
                    rationale="Contract removes cap required by policy section 4.",
                    confidence=0.9,
                )
            ]
        )

    monkeypatch.setattr(section_compare_llm, "get_review_model", lambda **_: object())
    monkeypatch.setattr(section_compare_llm, "invoke_structured", _fake_invoke)

    section = _section("s1", contract_text)
    hits = {"s1": [_policy_hit(policy_text)]}
    items, warnings = await section_compare_llm.compare_section_batch([section], hits)
    assert len(items) == 1
    assert items[0].policy_document_id
    assert items[0].status == ComplianceStatus.NON_COMPLIANT


@pytest.mark.asyncio
async def test_compare_rejects_cross_section_contract_quote(monkeypatch):
    section_text = "Human rights obligations for suppliers."
    wrong_quote = "Managed security services provider obligations."

    async def _fake_invoke(_model, _schema, *, system, user):
        return BatchSectionCompareLLMResult(
            items=[
                SectionCompareItem(
                    section_id="s1",
                    policy_section_id="4",
                    dimension_label="Human Rights",
                    status=ComplianceStatus.NON_COMPLIANT,
                    severity=Severity.CRITICAL,
                    contract_quote=wrong_quote,
                    policy_quote="",
                    rationale="Missing HR clause.",
                    confidence=0.9,
                )
            ]
        )

    monkeypatch.setattr(section_compare_llm, "get_review_model", lambda **_: object())
    monkeypatch.setattr(section_compare_llm, "invoke_structured", _fake_invoke)

    from review_agent.config import ReviewSettings

    section = _section("s1", section_text)
    items, _warnings = await section_compare_llm.compare_section_batch(
        [section],
        {"s1": [_policy_hit("Policy requires HR standards.")]},
        settings=ReviewSettings(grounding_downgrade_mode="inconclusive"),
    )
    assert len(items) == 1
    assert items[0].status == ComplianceStatus.INCONCLUSIVE
    assert items[0].contract_quote == ""


@pytest.mark.asyncio
async def test_compare_drops_unknown_section_id(monkeypatch):
    async def _fake_invoke(_model, _schema, *, system, user):
        return BatchSectionCompareLLMResult(
            items=[
                SectionCompareItem(
                    section_id="unknown",
                    policy_section_id="4",
                    dimension_label="Gap",
                    status=ComplianceStatus.NON_COMPLIANT,
                    severity=Severity.IMPORTANT,
                    contract_quote="Some contract text long enough for review.",
                    policy_quote="Policy text.",
                    rationale="Issue.",
                    confidence=0.8,
                )
            ]
        )

    monkeypatch.setattr(section_compare_llm, "get_review_model", lambda **_: object())
    monkeypatch.setattr(section_compare_llm, "invoke_structured", _fake_invoke)

    section = _section("s1", "Some contract text long enough for review.")
    items, warnings = await section_compare_llm.compare_section_batch(
        [section],
        {"s1": [_policy_hit("Policy text.")]},
    )
    assert items == []
    assert any("unknown section_id" in w for w in warnings)


@pytest.mark.asyncio
async def test_compare_failure_emits_inconclusive_with_hits(monkeypatch):
    async def _fake_invoke(*_args, **_kwargs):
        raise RuntimeError("llm unavailable")

    monkeypatch.setattr(section_compare_llm, "get_review_model", lambda **_: object())
    monkeypatch.setattr(section_compare_llm, "invoke_structured", _fake_invoke)

    section = _section("s1", "Some contract text long enough for review.")
    items, _warnings = await section_compare_llm.compare_section_batch(
        [section], {"s1": [_policy_hit("policy")]}
    )
    assert len(items) == 1
    assert items[0].status == ComplianceStatus.INCONCLUSIVE


@pytest.mark.asyncio
async def test_compare_failure_emits_insufficient_without_hits(monkeypatch):
    async def _fake_invoke(*_args, **_kwargs):
        raise RuntimeError("llm unavailable")

    monkeypatch.setattr(section_compare_llm, "get_review_model", lambda **_: object())
    monkeypatch.setattr(section_compare_llm, "invoke_structured", _fake_invoke)

    section = _section("s1", "Some contract text long enough for review.")
    items, _warnings = await section_compare_llm.compare_section_batch([section], {"s1": []})
    assert len(items) == 1
    assert items[0].status == ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT


@pytest.mark.asyncio
async def test_compare_batch_429_no_single_fanout(monkeypatch):
    from review_agent.config import ReviewSettings, get_settings
    from review_agent.models import llm_gateway

    sections = [
        _section("s1", "Liability is unlimited for all claims."),
        _section("s2", "Termination requires thirty days notice."),
    ]
    calls = {"n": 0}
    settings = ReviewSettings(compare_batch_retry_single=True, llm_review_posture_enabled=True)

    async def _fake_invoke(_model, _schema, *, system, user):
        calls["n"] += 1
        raise RuntimeError("HTTP 429 rate limit exceeded")

    llm_gateway.reset_llm_limiter()
    get_settings.cache_clear()
    limiter = llm_gateway._get_limiter()
    limiter.rate_limit_events = 3

    monkeypatch.setattr(section_compare_llm, "get_review_model", lambda **_: object())
    monkeypatch.setattr(section_compare_llm, "invoke_structured", _fake_invoke)

    hits = {
        "s1": [_policy_hit("Liability shall not exceed twelve months fees.")],
        "s2": [_policy_hit("Either party may terminate with thirty days notice.")],
    }
    items, _warnings = await section_compare_llm.compare_section_batch(
        sections,
        hits,
        settings=settings,
    )
    assert calls["n"] == 1
    assert len(items) == 2
    assert items[0].status == ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT


@pytest.mark.asyncio
async def test_compare_batch_retry_single(monkeypatch):
    from review_agent.config import ReviewSettings

    sections = [
        _section("s1", "Liability is unlimited for all claims."),
        _section("s2", "Termination requires thirty days notice."),
    ]
    calls = {"n": 0}
    settings = ReviewSettings(compare_batch_retry_single=True)

    async def _fake_invoke(_model, _schema, *, system, user):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("batch schema fail")
        if "s1" in user:
            return BatchSectionCompareLLMResult(
                items=[
                    SectionCompareItem(
                        section_id="s1",
                        policy_section_id="4",
                        dimension_label="Liability",
                        status=ComplianceStatus.NON_COMPLIANT,
                        severity=Severity.CRITICAL,
                        contract_quote="Liability is unlimited for all claims.",
                        policy_quote="Liability shall not exceed twelve months fees.",
                        rationale="Contract removes cap required by policy.",
                        confidence=0.9,
                    )
                ]
            )
        if "s2" in user:
            return BatchSectionCompareLLMResult(
                items=[
                    SectionCompareItem(
                        section_id="s2",
                        policy_section_id="4",
                        dimension_label="Termination",
                        status=ComplianceStatus.COMPLIANT,
                        severity=Severity.INFO,
                        contract_quote="Termination requires thirty days notice.",
                        policy_quote="Either party may terminate with thirty days notice.",
                        rationale="Notice period matches policy.",
                        confidence=0.9,
                    )
                ]
            )
        raise AssertionError(f"unexpected invoke: {user}")

    monkeypatch.setattr(section_compare_llm, "get_review_model", lambda **_: object())
    monkeypatch.setattr(section_compare_llm, "invoke_structured", _fake_invoke)

    hits = {
        "s1": [_policy_hit("Liability shall not exceed twelve months fees.")],
        "s2": [_policy_hit("Either party may terminate with thirty days notice.")],
    }
    items, _warnings = await section_compare_llm.compare_section_batch(
        sections,
        hits,
        settings=settings,
    )
    assert calls["n"] == 3
    assert len(items) == 2


@pytest.mark.asyncio
async def test_format_sections_primary_only_single_policy(monkeypatch):
    from review_agent.config import ReviewSettings

    contract_text = "Security controls are optional for the supplier."
    policy_hr = "Human rights standards apply to all suppliers."
    policy_sec = "Managed security services must meet MSS requirements."

    async def _fake_invoke(_model, _schema, *, system, user):
        assert policy_hr not in user
        assert policy_sec in user
        assert "**Policy 2**" not in user
        return BatchSectionCompareLLMResult(
            items=[
                SectionCompareItem(
                    section_id="s1",
                    policy_section_id="4",
                    dimension_label="Security",
                    status=ComplianceStatus.NON_COMPLIANT,
                    severity=Severity.CRITICAL,
                    contract_quote="Security controls are optional for the supplier.",
                    policy_quote="Managed security services must meet MSS requirements.",
                    rationale="Contract makes security optional.",
                    confidence=0.9,
                )
            ]
        )

    monkeypatch.setattr(section_compare_llm, "get_review_model", lambda **_: object())
    monkeypatch.setattr(section_compare_llm, "invoke_structured", _fake_invoke)

    section = _section("s1", contract_text)
    hits = {
        "s1": [
            _policy_hit(policy_hr, categories=["human_resources"]),
            _policy_hit(policy_sec, categories=["security"]),
        ]
    }
    settings = ReviewSettings(
        compare_policy_hit_mode="category_aligned",
        compare_max_policy_hits=3,
    )
    items, _stats, batch_stats = await section_compare_llm.compare_all_sections(
        [section],
        hits,
        settings=settings,
        categories_by_section={"s1": ["security"]},
    )
    assert len(items) == 1
    assert batch_stats["compare_hit_selection"]["category_aligned_sections"] == 1


@pytest.mark.asyncio
async def test_compare_includes_related_excerpts(monkeypatch):
    from review_agent.services.section_cross_reference import RelatedSectionBundle

    contract_text = "This Agreement continues for three years. Sections 3 through 10 survive."
    policy_text = "NDA term shall be no less than five years."
    captured = {"user": ""}

    async def _fake_invoke(_model, _schema, *, system, user):
        captured["user"] = user
        return BatchSectionCompareLLMResult(
            items=[
                SectionCompareItem(
                    section_id="5",
                    policy_section_id="1",
                    dimension_label="Term",
                    status=ComplianceStatus.COMPLIANT,
                    severity=Severity.INFO,
                    contract_quote=contract_text[:80],
                    policy_quote=policy_text,
                    rationale="Survival incorporates confidentiality term.",
                    confidence=0.85,
                )
            ]
        )

    monkeypatch.setattr(section_compare_llm, "get_review_model", lambda **_: object())
    monkeypatch.setattr(section_compare_llm, "invoke_structured", _fake_invoke)

    section = _section("5", contract_text)
    hits = {"5": [_policy_hit(policy_text, categories=["confidentiality"])]}
    related = {
        "5": RelatedSectionBundle(
            primary_section_id="5",
            related=[
                (
                    "4",
                    "Protection",
                    "five (5) years thereafter each party shall protect Confidential Information.",
                )
            ],
            resolution_reason="survival_3_10",
        )
    }
    items, _warnings = await section_compare_llm.compare_section_batch(
        [section],
        hits,
        related_by_section=related,
    )
    assert items
    assert "five (5) years" in captured["user"]
    assert "Related contract sections" in captured["user"]

