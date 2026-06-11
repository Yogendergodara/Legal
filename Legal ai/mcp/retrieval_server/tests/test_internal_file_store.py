"""Tests for file-based internal document store."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from mcp.retrieval_server.integrations import internal_file_store


@pytest.fixture
def store_root(tmp_path: Path) -> Path:
    return tmp_path / "internal_docs"


def test_ingest_and_search(store_root: Path) -> None:
    text = "Confidentiality obligations apply for a period of two years."
    content_hash = hashlib.sha256(text.encode()).hexdigest()
    result = internal_file_store.ingest_document(
        tenant_id="demo-tenant",
        title="NDA Policy",
        doc_text=text,
        content_hash=content_hash,
        root=store_root,
    )
    assert result["deduped"] is False
    assert result["tenant_id"] == "demo-tenant"
    assert result["source_id"].startswith("internal:")

    hits = internal_file_store.search_documents(
        "confidentiality two years",
        tenant_id="demo-tenant",
        max_results=5,
        root=store_root,
    )
    assert len(hits) == 1
    assert hits[0]["title"] == "NDA Policy"


def test_dedupes_by_content_hash(store_root: Path) -> None:
    text = "Same document body"
    content_hash = hashlib.sha256(text.encode()).hexdigest()
    first = internal_file_store.ingest_document(
        tenant_id="demo-tenant",
        title="Doc A",
        doc_text=text,
        content_hash=content_hash,
        root=store_root,
    )
    second = internal_file_store.ingest_document(
        tenant_id="demo-tenant",
        title="Doc B",
        doc_text=text,
        content_hash=content_hash,
        root=store_root,
    )
    assert second["deduped"] is True
    assert second["source_id"] == first["source_id"]
