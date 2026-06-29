"""Tests for reranker ops rollup in section retrieval node (Phase 22 P5)."""

from __future__ import annotations

from uuid import UUID

import pytest
from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk, RetrievalHit

from review_agent.clients.document_client import DocumentMCPClient
from review_agent.graph.section_retrieval_nodes import section_policy_retrieval_node
from review_agent.schemas.section_classify import SectionCategoryResult
from review_agent.schemas.section_retrieval import SectionRetrievalBundle
from review_agent.state.review_state import ReviewState


def _section(section_id: str, text: str) -> IndexedChunk:
    return IndexedChunk(
        chunk_id=f"c-{section_id}",
        document_id=UUID("00000000-0000-0000-0000-000000000001"),
        tenant_id="demo",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id=section_id,
        section_path=section_id,
        title=section_id,
        text=text,
    )


def _hit(text: str) -> RetrievalHit:
    return RetrievalHit(
        parent_chunk=IndexedChunk(
            chunk_id="p1",
            document_id=UUID("00000000-0000-0000-0000-000000000002"),
            tenant_id="demo",
            kind=DocumentKind.POLICY,
            chunk_role=ChunkRole.PARENT,
            section_id="pol",
            section_path="pol",
            title="Policy",
            text=text,
        ),
        score=1.0,
    )


@pytest.mark.asyncio
async def test_retrieval_node_rerank_rollup(monkeypatch) -> None:
    sections = [
        _section("s1", "Security obligations for managed services and encryption controls."),
        _section("s2", "Human rights standards for suppliers and workers in the supply chain."),
    ]

    async def _fake_classify(_sections, **kwargs):
        return {
            sid: SectionCategoryResult(
                section_id=sid,
                categories=["security"],
                query_terms=[sid],
            )
            for sid in ("s1", "s2")
        }, {}

    async def _fake_retrieve(*_args, section, **_kwargs):
        meta = {"reranker_used": "cross_encoder", "reranker_backend": "cross_encoder"}
        if section.section_id == "s2":
            meta = {"reranker_used": "lexical_fallback", "reranker_backend": "cross_encoder"}
        return SectionRetrievalBundle(
            section_id=section.section_id,
            categories=["security"],
            policy_hits=[_hit("Policy text")],
            retrieval_meta=meta,
        )

    monkeypatch.setattr(
        "review_agent.graph.section_retrieval_nodes.classify_all_sections",
        _fake_classify,
    )
    monkeypatch.setattr(
        "review_agent.graph.section_retrieval_nodes.multi_retrieve_for_section",
        _fake_retrieve,
    )

    state: ReviewState = {
        "tenant_id": "demo",
        "contract_type": "msa",
        "contract_sections": sections,
        "policy_document_ids": [],
        "discovered_policy_document_ids": [],
        "compliance_stats": {},
    }

    client = DocumentMCPClient("http://localhost:8003")
    result = await section_policy_retrieval_node(state, client)
    stats = result["compliance_stats"]
    assert stats["reranker_cross_encoder_sections"] == 1
    assert stats["reranker_lexical_fallback_sections"] == 1
    assert stats["reranker_backend_config"] in ("cross_encoder", "lexical", "off")
