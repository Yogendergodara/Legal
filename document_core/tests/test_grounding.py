"""Strict section-scoped quote grounding (Phase 21 P1-Q)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from document_core.schemas.chunk import (
    ChunkRole,
    DocumentKind,
    GroundingCheckRequest,
    IndexedChunk,
)
from document_core.services.grounding import verify_quote


def _parent(section_id: str, text: str, *, document_id=None) -> IndexedChunk:
    doc_id = document_id or uuid4()
    return IndexedChunk(
        chunk_id=f"c-{section_id}",
        document_id=doc_id,
        tenant_id="demo",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id=section_id,
        section_path=section_id,
        title=section_id,
        text=text,
    )


class _FakeStore:
    def __init__(
        self,
        *,
        sections: list[IndexedChunk],
        canonical: str = "",
    ) -> None:
        self._sections = {s.section_id: s for s in sections}
        self._canonical = canonical
        self._document_id = sections[0].document_id if sections else uuid4()

    def get_parent_by_section(
        self,
        tenant_id: str,
        document_id,
        section_id: str,
    ) -> IndexedChunk | None:
        return self._sections.get(section_id)

    def get_canonical_text(self, tenant_id: str, document_id) -> str | None:
        return self._canonical or None

    def get_parents(self, tenant_id: str, document_id) -> list[IndexedChunk]:
        return list(self._sections.values())


@pytest.mark.asyncio
async def test_verify_quote_strict_section_match():
    doc_id = uuid4()
    s3 = _parent("3", "Human rights and labor standards apply.", document_id=doc_id)
    s8 = _parent("8", "Managed security services provider obligations.", document_id=doc_id)
    store = _FakeStore(
        sections=[s3, s8],
        canonical=f"{s3.text}\n\n{s8.text}",
    )
    result = await verify_quote(
        GroundingCheckRequest(
            tenant_id="demo",
            document_id=doc_id,
            quote="Human rights and labor",
            section_id="3",
        ),
        store=store,
    )
    assert result.grounded is True
    assert result.section_id == "3"


@pytest.mark.asyncio
async def test_verify_quote_rejects_other_section():
    doc_id = uuid4()
    s3 = _parent("3", "Human rights and labor standards apply.", document_id=doc_id)
    s8 = _parent("8", "Managed security services provider obligations.", document_id=doc_id)
    store = _FakeStore(
        sections=[s3, s8],
        canonical=f"{s3.text}\n\n{s8.text}",
    )
    result = await verify_quote(
        GroundingCheckRequest(
            tenant_id="demo",
            document_id=doc_id,
            quote="Managed security services",
            section_id="3",
        ),
        store=store,
    )
    assert result.grounded is False
    assert result.section_id == "3"
    assert "section text" in result.message.lower()


@pytest.mark.asyncio
async def test_verify_quote_document_wide_without_section_id():
    doc_id = uuid4()
    s3 = _parent("3", "Human rights and labor standards apply.", document_id=doc_id)
    s8 = _parent("8", "Managed security services provider obligations.", document_id=doc_id)
    store = _FakeStore(
        sections=[s3, s8],
        canonical=f"{s3.text}\n\n{s8.text}",
    )
    result = await verify_quote(
        GroundingCheckRequest(
            tenant_id="demo",
            document_id=doc_id,
            quote="Managed security services",
            section_id=None,
        ),
        store=store,
    )
    assert result.grounded is True
    assert result.section_id in {"8", "3", None}


@pytest.mark.asyncio
async def test_verify_quote_matches_bullet_list_quote():
    doc_id = uuid4()
    text = (
        "Support and respect internationally proclaimed human rights; "
        "Ensure all work is performed voluntarily"
    )
    section = _parent("5.2", text, document_id=doc_id)
    store = _FakeStore(sections=[section], canonical=text)
    result = await verify_quote(
        GroundingCheckRequest(
            tenant_id="demo",
            document_id=doc_id,
            quote="• Support and respect internationally proclaimed human rights",
            section_id="5.2",
        ),
        store=store,
    )
    assert result.grounded is True
