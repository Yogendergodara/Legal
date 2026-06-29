"""P0-2.4: classifier fallback warnings surface in section retrieval node."""

from __future__ import annotations

from uuid import UUID

import pytest

from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk
from review_agent.clients.document_client import DocumentMCPClient
from review_agent.graph.section_retrieval_nodes import section_policy_retrieval_node
from review_agent.schemas.section_classify import SectionCategoryResult
from review_agent.state.review_state import ReviewState


def _section(section_id: str, title: str, text: str) -> IndexedChunk:
    return IndexedChunk(
        chunk_id=f"c-{section_id}",
        document_id=UUID("00000000-0000-0000-0000-000000000001"),
        tenant_id="demo",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id=section_id,
        section_path=section_id,
        title=title,
        text=text,
    )


@pytest.mark.asyncio
async def test_classifier_fallback_warning_in_retrieval_node(monkeypatch) -> None:
    section = _section("3", "Limitation of Liability", "Total liability shall not exceed one hundred thousand dollars.")

    async def _fake_classify(_sections, **kwargs):
        return {
            "3": SectionCategoryResult(
                section_id="3",
                categories=["general"],
                query_terms=["limitation of liability"],
                classify_warning="No module named 'langchain'",
            )
        }, {}

    async def _fake_retrieve(*_args, **_kwargs):
        from review_agent.schemas.section_retrieval import SectionRetrievalBundle

        return SectionRetrievalBundle(
            section_id="3",
            categories=["general"],
            policy_hits=[],
            retrieval_meta={},
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
        "contract_type": "nda",
        "contract_sections": [section],
        "policy_document_ids": [],
        "discovered_policy_document_ids": [],
        "compliance_stats": {},
    }

    client = DocumentMCPClient("http://localhost:8003")
    result = await section_policy_retrieval_node(state, client)

    assert any("classifier fallback" in w for w in result["warnings"])
    assert any("langchain" in w for w in result["warnings"])
    failed = result.get("failed_sections") or []
    assert any(
        entry.get("section_id") == "3" and entry.get("error_code") == "retrieval_zero_hit"
        for entry in failed
    )
