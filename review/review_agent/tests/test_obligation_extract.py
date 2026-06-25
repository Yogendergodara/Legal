"""Tests for obligation extraction (Phase R1)."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from document_core.schemas.chunk import DocumentKind, ChunkRole, IndexedChunk
from review_agent.config import ReviewSettings
from review_agent.graph.obligation_nodes import obligation_extract_node
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
