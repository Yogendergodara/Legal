"""Tests for memorandum source post-processing."""

from deep_research_from_scratch.report_sources import build_case_digest, ensure_sources_section
from deep_research_from_scratch.source_registry import RetrievedSource


def test_build_case_digest_includes_full_urls():
    sources = [
        RetrievedSource(
            url="https://indiankanoon.org/doc/123456/",
            title="Test v State",
            fetched=True,
            excerpt="The court held that indefinite freeze is impermissible.",
        )
    ]
    digest = build_case_digest(sources)
    assert "https://indiankanoon.org/doc/123456/" in digest
    assert "indefinite freeze" in digest


def test_ensure_sources_section_appends_full_urls():
    report = "## Discussion\nSome analysis.\n\n## Disclaimer\nNot legal advice."
    sources = [
        RetrievedSource(
            url="https://indiankanoon.org/doc/999/",
            title="Alpha v Beta",
            fetched=True,
        )
    ]
    out = ensure_sources_section(report, sources)
    assert "### Sources" in out
    assert "[1] Alpha v Beta: https://indiankanoon.org/doc/999/" in out
