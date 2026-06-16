"""Tests for the pluggable memory backend and keyword search retrieval."""

import pytest

import deep_research_from_scratch.memory_backend as mb
from deep_research_from_scratch.memory_backend import (
    FileMemoryBackend,
    MemoryHit,
    _keyword_score,
    format_hits,
    get_memory_backend,
)
from deep_research_from_scratch.memory_tools import (
    get_auto_mem_path,
    record_transcript,
)


@pytest.fixture(autouse=True)
def reset_backend_singleton():
    """Reset the backend singleton before and after each test."""
    mb._BACKEND_SINGLETON = None
    yield
    mb._BACKEND_SINGLETON = None


def test_keyword_score():
    """Verify keyword scoring ranks relevance based on matching query terms."""
    haystack = "The quick brown fox jumps over the lazy dog."
    assert _keyword_score(haystack, ["fox", "dog"]) == 2
    assert _keyword_score(haystack, [t.lower() for t in ["FOX", "DOG"]]) == 2  # Case insensitive
    assert _keyword_score(haystack, ["cat", "rabbit"]) == 0
    assert _keyword_score("", ["fox"]) == 0


def test_file_memory_backend_longterm(configure_test_memory_dir):
    """Verify longterm search retrieves matching markdown files in memory directory."""
    auto_dir = get_auto_mem_path()
    
    # Create mock memory files
    (auto_dir / "acme_corp.md").write_text(
        "# Acme Corp Profile\nAcme Corp is based in California.", encoding="utf-8"
    )
    (auto_dir / "tax_rules.md").write_text(
        "# Tax Guidance\nSection 80C provides tax deductions.", encoding="utf-8"
    )
    (auto_dir / "MEMORY.md").write_text(
        "# Memory Index\n- [Acme](acme_corp.md)\n", encoding="utf-8"
    )  # Should be skipped as ENTRYPOINT_NAME

    backend = FileMemoryBackend()
    
    # Search for "California"
    hits = backend.search_longterm("California")
    assert len(hits) == 1
    assert hits[0].source == "acme_corp.md"
    assert "California" in hits[0].text
    
    # Search for "tax deductions"
    hits = backend.search_longterm("tax deductions")
    assert len(hits) == 1
    assert hits[0].source == "tax_rules.md"
    
    # Search for non-existent term
    hits = backend.search_longterm("something_random")
    assert len(hits) == 0


def test_file_memory_backend_session(configure_test_memory_dir):
    """Verify session search retrieves matching turns from session transcripts."""
    session_id = "test_session_99"
    
    # Write some transcript lines
    record_transcript(session_id, "user", "I want to ask about non-compete clauses.")
    record_transcript(session_id, "assistant", "Sure, non-compete clauses are void under Section 27.")
    record_transcript(session_id, "user", "Are there any exceptions?")

    backend = FileMemoryBackend()
    
    # Search for "non-compete"
    hits = backend.search_session(session_id, "non-compete")
    assert len(hits) == 2
    assert any("[user]" in h.text for h in hits)
    assert any("[assistant]" in h.text for h in hits)
    assert all(h.source == session_id for h in hits)

    # Search for "exceptions"
    hits = backend.search_session(session_id, "exceptions")
    assert len(hits) == 1
    assert "exceptions" in hits[0].text


def test_pluggable_backend_singleton(monkeypatch):
    """Verify backend singleton instantiation and selector validations."""
    monkeypatch.setenv("MEMORY_BACKEND", "file")
    mb._BACKEND_SINGLETON = None
    backend1 = get_memory_backend()
    assert isinstance(backend1, FileMemoryBackend)

    backend2 = get_memory_backend()
    assert backend1 is backend2  # Verify singleton behavior

    # Validate raise on pgvector/qdrant
    monkeypatch.setenv("MEMORY_BACKEND", "pgvector")
    mb._BACKEND_SINGLETON = None
    with pytest.raises(NotImplementedError, match="MEMORY_BACKEND='pgvector' is not implemented"):
        get_memory_backend()

    # Validate raise on unknown backend
    monkeypatch.setenv("MEMORY_BACKEND", "unknown")
    mb._BACKEND_SINGLETON = None
    with pytest.raises(ValueError, match="Unknown MEMORY_BACKEND='unknown'"):
        get_memory_backend()


def test_format_hits():
    """Verify memory hits are formatted into clean text blocks."""
    # Test empty list
    assert format_hits([], empty="Nothing found") == "Nothing found"

    # Test formatted hits
    hits = [
        MemoryHit(text="Line 1 details", source="doc1.md"),
        MemoryHit(text="Line 2 details", source="doc2.md"),
    ]
    result = format_hits(hits)
    assert "--- doc1.md ---" in result
    assert "Line 1 details" in result
    assert "--- doc2.md ---" in result
    assert "Line 2 details" in result
