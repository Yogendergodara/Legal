"""Boilerplate obligation detection (Phase R1)."""

from __future__ import annotations

from uuid import UUID

from review_agent.services.section_gap_status import (
    is_boilerplate_section,
    normalize_section_title,
)
from document_core.schemas.chunk import DocumentKind, ChunkRole, IndexedChunk

BOILERPLATE_OBLIGATION_TYPES = frozenset({
    "governing_law",
    "notices",
    "counterparts",
    "severability",
    "entire_agreement",
    "assignment",
    "signatures",
    "boilerplate",
})


def infer_obligation_boilerplate(
    *,
    text: str,
    section_title: str,
    obligation_type: str = "",
) -> bool:
    otype = (obligation_type or "").strip().lower()
    if otype in BOILERPLATE_OBLIGATION_TYPES:
        return True
    title = normalize_section_title(section_title)
    lowered = f"{title} {text}".lower()
    if "governing law" in lowered or "jurisdiction" in title.lower():
        return True
    stub = IndexedChunk(
        chunk_id="stub",
        document_id=UUID("00000000-0000-0000-0000-000000000001"),
        tenant_id="stub",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id="stub",
        section_path="stub",
        title=section_title,
        text=text,
    )
    return is_boilerplate_section(stub)


def section_title_is_boilerplate(section_title: str) -> bool:
    stub = IndexedChunk(
        chunk_id="stub",
        document_id=UUID("00000000-0000-0000-0000-000000000001"),
        tenant_id="stub",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id="stub",
        section_path="stub",
        title=section_title,
        text="",
    )
    title = normalize_section_title(section_title)
    if "governing law" in title.lower():
        return True
    return is_boilerplate_section(stub)
