"""Tests for section lexical classifier."""

from uuid import UUID

from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk

from review_agent.services.section_classifier import classify_section_lexical


def _section(title: str, text: str, section_id: str = "s1") -> IndexedChunk:
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


def test_classify_liability_section():
    section = _section(
        "Limitation of Liability",
        "The total liability shall not exceed fees paid in twelve months.",
    )
    result = classify_section_lexical(section)
    assert "liability" in result.categories
    assert result.query_terms


def test_classify_unknown_defaults_general():
    section = _section("Definitions", "Party means the signatory entity.")
    result = classify_section_lexical(section)
    assert result.categories == ["general"]
