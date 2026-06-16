"""Tests for structured source registry helpers."""

from deep_research_from_scratch.source_registry import (
    RetrievedSource,
    build_verification_corpus,
    classify_authority_tier,
    extract_case_names,
    extract_citations,
    filter_citable_sources,
    format_writer_source_registry,
    is_blocked_fetch_content,
    is_paywall_url,
    merge_retrieved_sources,
    normalize_url,
    source_from_fetch,
    sources_from_search_hits,
)


def test_normalize_url_strips_trailing_slash():
    assert normalize_url("https://indiankanoon.org/doc/123/") == normalize_url(
        "https://indiankanoon.org/doc/123"
    )


def test_classify_authority_tier_primary_for_indian_kanoon():
    tier = classify_authority_tier(
        "https://indiankanoon.org/doc/123/",
        {"backend": "indiankanoon"},
    )
    assert tier == "primary"


def test_classify_authority_tier_unknown_for_blog():
    tier = classify_authority_tier("https://www.lawsikho.com/blog/murder-law")
    assert tier == "unknown"


def test_sources_from_search_hits():
    hits = [
        {
            "url": "https://indiankanoon.org/doc/1/",
            "title": "Test Case",
            "text_snippet": "snippet",
            "metadata": {"citation": "2024 INSC 1", "backend": "indiankanoon"},
        }
    ]
    sources = sources_from_search_hits(hits)
    assert len(sources) == 1
    assert sources[0].citation == "2024 INSC 1"
    assert sources[0].authority_tier == "primary"
    assert sources[0].fetched is False


def test_source_from_fetch():
    src = source_from_fetch(
        "https://indiankanoon.org/doc/1/",
        {"full_text": "Virsa Singh v State of Punjab held that...", "title": "Virsa Singh"},
        excerpt_limit=1000,
    )
    assert src is not None
    assert src.fetched is True
    assert "Virsa Singh" in src.excerpt


def test_merge_retrieved_sources_prefers_fetched_excerpt():
    existing = RetrievedSource(
        url="https://indiankanoon.org/doc/1/",
        title="Case",
        fetched=False,
        excerpt="short",
    )
    incoming = RetrievedSource(
        url="https://indiankanoon.org/doc/1/",
        title="Case",
        fetched=True,
        excerpt="much longer fetched excerpt text",
    )
    merged = merge_retrieved_sources([existing], [incoming])
    assert len(merged) == 1
    assert merged[0].fetched is True
    assert "longer" in merged[0].excerpt


def test_build_verification_corpus_includes_raw_notes_and_sources():
    corpus = build_verification_corpus(
        notes=["compressed note"],
        raw_notes=["raw fetch output with (1973) 4 SCC 225"],
        sources=[
            RetrievedSource(
                url="https://indiankanoon.org/doc/1/",
                title="SCC case",
                fetched=True,
                excerpt="Virsa Singh v State of Punjab",
            )
        ],
    )
    assert "compressed note" in corpus
    assert "(1973) 4 SCC 225" in corpus
    assert "Virsa Singh" in corpus


def test_extract_case_names():
    text = "As held in Virsa Singh v State of Punjab, murder requires intention."
    names = extract_case_names(text)
    assert any("VIRSA SINGH" in name for name in names)


def test_extract_citations():
    text = "See (1973) 4 SCC 225 and 2024 INSC 1."
    citations = extract_citations(text)
    assert "(1973) 4 SCC 225" in citations
    assert "2024 INSC 1" in citations


def test_format_writer_source_registry():
    sources = [
        RetrievedSource(
            url="https://indiankanoon.org/doc/1/",
            title="Virsa Singh v State of Punjab",
            authority_tier="primary",
            fetched=True,
            citation="(1958) SCR 149",
            excerpt="Murder ingredients test.",
        )
    ]
    text = format_writer_source_registry(sources)
    assert "[1]" in text
    assert "FETCHED" in text
    assert "indiankanoon.org" in text


def test_redact_fabricated_citations():
    from deep_research_from_scratch.source_registry import redact_fabricated_citations

    report = "See AIR 1973 SC 1461 for the rule."
    redacted = redact_fabricated_citations(report, ["AIR 1973 SC 1461"])
    assert "AIR 1973 SC 1461" not in redacted
    assert "CITATION REMOVED" in redacted


def test_is_paywall_url():
    assert is_paywall_url("https://www.manupatra.com/doc/123")
    assert is_paywall_url("https://www.scconline.com/case/1")
    assert not is_paywall_url("https://indiankanoon.org/doc/1/")


def test_filter_citable_sources_excludes_unfetched_paywall():
    sources = [
        RetrievedSource(
            url="https://indiankanoon.org/doc/1/",
            title="Good case",
            fetched=True,
        ),
        RetrievedSource(
            url="https://www.manupatra.com/doc/2/",
            title="Paywall snippet",
            fetched=False,
        ),
    ]
    citable = filter_citable_sources(sources)
    assert len(citable) == 1
    assert "indiankanoon" in citable[0].url


def test_source_from_fetch_marks_access_denied():
    src = source_from_fetch(
        "https://www.manupatra.com/doc/1/",
        {"full_text": "Sign in to continue reading", "title": "Paywalled"},
        excerpt_limit=1000,
    )
    assert src is not None
    assert src.access_denied is True
    assert src.fetched is False


def test_is_blocked_fetch_content():
    assert is_blocked_fetch_content("")
    assert is_blocked_fetch_content("Sign in to continue")
    assert not is_blocked_fetch_content("Virsa Singh v State of Punjab held that murder requires intention.")
