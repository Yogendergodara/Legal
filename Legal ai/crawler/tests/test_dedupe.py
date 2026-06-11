"""Tests for content_hash deduplication."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from crawler.extraction import compute_content_hash
from crawler.storage import document_exists_by_hash, upsert_document


def test_compute_content_hash_deterministic() -> None:
    text = "The Supreme Court held that non-compete clauses are enforceable."
    h1 = compute_content_hash(text)
    h2 = compute_content_hash(text)
    assert h1 == h2
    assert len(h1) == 64


def test_dedupe_collapses_identical_content_hash() -> None:
    content = "Identical legal article text for deduplication test."
    content_hash = compute_content_hash(content)

    mock_session = MagicMock()
    mock_existing = MagicMock()
    mock_existing.id = 1
    mock_existing.content_hash = content_hash

    with patch("crawler.storage.document_exists_by_hash", return_value=True):
        with patch.object(mock_session, "execute") as mock_exec:
            mock_exec.return_value.scalar_one.return_value = mock_existing
            doc, deduped = upsert_document(
                mock_session,
                url="https://livelaw.in/article-1",
                canonical_url="https://livelaw.in/article-1",
                source_id=1,
                title="Article",
                clean_text=content,
                content_hash=content_hash,
            )

    assert deduped is True
    assert doc.content_hash == content_hash


def test_document_exists_by_hash() -> None:
    mock_session = MagicMock()
    mock_session.execute.return_value.scalar_one_or_none.return_value = 42
    assert document_exists_by_hash(mock_session, "abc123") is True

    mock_session.execute.return_value.scalar_one_or_none.return_value = None
    assert document_exists_by_hash(mock_session, "xyz") is False
