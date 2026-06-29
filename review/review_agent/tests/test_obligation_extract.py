"""Tests for obligation extraction (Phase R1)."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from document_core.schemas.chunk import DocumentKind, ChunkRole, IndexedChunk
from review_agent.config import ReviewSettings
from review_agent.graph.obligation_nodes import _cap_obligations_fair, obligation_extract_node
from review_agent.schemas.obligation import ContractObligation
from review_agent.services.obligation_boilerplate import infer_obligation_boilerplate
from review_agent.services.obligation_extract import extract_obligations_batch, _fallback_obligations
from review_agent.services.named_policy_routing import extract_named_policy_title_keys


def _section(section_id: str, title: str, text: str) -> IndexedChunk:
    return IndexedChunk(
        chunk_id=f"{section_id}:p",
        document_id=uuid4(),
        tenant_id="test",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id=section_id,
        section_path=section_id,
        title=title,
        text=text,
    )


@pytest.mark.asyncio
async def test_obligation_fallback_one_per_section(monkeypatch):
    async def _fail(*_args, **_kwargs):
        raise RuntimeError("llm down")

    monkeypatch.setattr("review_agent.services.obligation_extract.invoke_structured", _fail)
    section = _section("2.1", "Security", "Must implement encryption controls.")
    result = await extract_obligations_batch(
        [section],
        settings=ReviewSettings(obligation_extract_batch_size=1),
    )
    assert len(result.obligations) == 1
    assert result.obligations[0].extract_source == "fallback"


def test_obligation_explicit_mention():
    text = "Party shall comply with Xecurify's Security Practices Policy."
    keys = extract_named_policy_title_keys(text)
    assert keys
    section = _section("2.3", "Security Measures", text)
    obs = _fallback_obligations(section)
    assert obs[0].explicit_policy_mentions


def test_obligation_boilerplate_governing_law():
    assert infer_obligation_boilerplate(
        text="Governed by Wyoming law.",
        section_title="10.1 Governing Law",
        obligation_type="governing_law",
    )


def test_obligation_boilerplate_notices():
    assert infer_obligation_boilerplate(
        text="All notices shall be in writing.",
        section_title="10.5 Notices",
        obligation_type="notices",
    )


@pytest.mark.asyncio
async def test_obligation_mixed_section(monkeypatch):
    body = (
        "Receiving Party shall implement security measures consistent with "
        "Xecurify's Security Practices Policy. "
        "Recipient shall retain logs for seven years."
    )
    section = _section("2.3", "Security and Retention", body)

    async def _mock_invoke(model, schema, *, system, user):
        from review_agent.schemas.obligation import BatchObligationExtractResult, SectionObligationExtractResult
        from review_agent.schemas.obligation import ObligationExtractItem

        return BatchObligationExtractResult(
            sections=[
                SectionObligationExtractResult(
                    section_id="2.3",
                    obligations=[
                        ObligationExtractItem(
                            index=0,
                            text="Receiving Party shall implement security measures consistent with Xecurify's Security Practices Policy.",
                            obligation_type="security_controls",
                            explicit_policy_mentions=["Security Practices Policy"],
                        ),
                        ObligationExtractItem(
                            index=1,
                            text="Recipient shall retain logs for seven years.",
                            obligation_type="data_retention",
                        ),
                    ],
                )
            ]
        )

    monkeypatch.setattr("review_agent.services.obligation_extract.get_review_model", lambda **_kw: object())
    monkeypatch.setattr("review_agent.services.obligation_extract.invoke_structured", _mock_invoke)
    result = await extract_obligations_batch([section], settings=ReviewSettings())
    assert len(result.obligations) >= 2


@pytest.mark.asyncio
async def test_graph_node_skips_extract_when_routing_off(monkeypatch):
    state = {
        "tenant_id": "cisco-beta",
        "contract_sections": [_section("1", "A", "text")],
        "compliance_stats": {},
    }
    monkeypatch.setattr(
        "review_agent.graph.obligation_nodes.get_settings",
        lambda: ReviewSettings(
            obligation_routing_enabled=False,
            obligation_extract_enabled=True,
        ),
    )
    extract_called = {"n": 0}

    async def _boom(*_args, **_kwargs):
        extract_called["n"] += 1
        raise RuntimeError("should not call LLM")

    monkeypatch.setattr(
        "review_agent.graph.obligation_nodes.extract_obligations_batch",
        _boom,
    )
    out = await obligation_extract_node(state, client=None)  # type: ignore[arg-type]
    assert extract_called["n"] == 0
    assert out["obligations"] == []
    assert out["obligation_extract_stats"]["obligation_extract_skip_reason"] == "routing_off"


@pytest.mark.asyncio
async def test_graph_node_flag_off(monkeypatch):
    state = {
        "tenant_id": "t",
        "contract_sections": [_section("1", "A", "text")],
        "compliance_stats": {},
    }
    monkeypatch.setattr(
        "review_agent.graph.obligation_nodes.get_settings",
        lambda: ReviewSettings(obligation_extract_enabled=False),
    )
    out = await obligation_extract_node(state, client=None)  # type: ignore[arg-type]
    assert out == {}


@pytest.mark.asyncio
async def test_extract_batch_fail_retries_single(monkeypatch):
    s1 = _section("1", "Fees", "Customer must pay all fees within thirty days.")
    s2 = _section("2", "Security", "Vendor shall maintain encryption controls.")
    calls = {"batch": 0, "single": 0}

    async def _mock_invoke(model, schema, *, system, user):
        from review_agent.schemas.obligation import (
            BatchObligationExtractResult,
            ObligationExtractItem,
            SectionObligationExtractResult,
        )

        if "Section 1" in user and "Section 2" in user:
            calls["batch"] += 1
            raise ValueError("batch fail")
        calls["single"] += 1
        section_id = "1" if "Section 1" in user else "2"
        return BatchObligationExtractResult(
            sections=[
                SectionObligationExtractResult(
                    section_id=section_id,
                    obligations=[
                        ObligationExtractItem(
                            index=0,
                            text=(s1.text if section_id == "1" else s2.text) or "",
                            obligation_type="payment" if section_id == "1" else "security",
                        )
                    ],
                )
            ]
        )

    monkeypatch.setattr("review_agent.services.obligation_extract.get_review_model", lambda **_kw: object())
    monkeypatch.setattr("review_agent.services.obligation_extract.invoke_structured", _mock_invoke)
    result = await extract_obligations_batch(
        [s1, s2],
        settings=ReviewSettings(obligation_extract_batch_size=2),
    )
    assert calls["batch"] == 1
    assert calls["single"] == 2
    assert result.extract_batch_failures == 1
    assert result.extract_single_retries == 2
    assert result.extract_single_recovered == 2
    assert len(result.obligations) == 2
    assert all(ob.extract_source == "llm" for ob in result.obligations)


def test_cap_obligations_fair_preserves_section_coverage():
    obligations = [
        ContractObligation(
            obligation_id=f"{sid}-o{i}",
            section_id=sid,
            text="obligation text",
        )
        for sid in ("1", "2", "3")
        for i in range(10)
    ]
    capped, dropped_count, dropped_ids = _cap_obligations_fair(
        obligations,
        max_total=12,
        max_per_section=4,
        section_order=["1", "2", "3"],
    )
    assert len(capped) == 12
    assert dropped_count == 18
    assert len({ob.section_id for ob in capped}) == 3
    assert dropped_ids
