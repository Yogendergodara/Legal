"""Tests for RC-03 catalog scope helpers."""

from __future__ import annotations

from review_agent.services.catalog_registry import CatalogEntry
from review_agent.services.routing_scope import (
    filter_catalog_entries,
    review_catalog_doc_ids,
)


def _entry(document_id: str) -> CatalogEntry:
    return CatalogEntry(
        document_id=document_id,
        policy_ref=f"ref-{document_id}",
        title=f"Policy {document_id}",
        aliases=[],
        topics=[],
        summary="",
    )


def test_review_catalog_doc_ids_prefers_policy_document_ids():
    state = {
        "policy_document_ids": ["a", "b"],
        "discovered_policy_document_ids": ["c"],
    }
    assert review_catalog_doc_ids(state) == {"a", "b"}


def test_review_catalog_doc_ids_falls_back_to_discovered():
    state = {"discovered_policy_document_ids": ["c", "d"]}
    assert review_catalog_doc_ids(state) == {"c", "d"}


def test_review_catalog_doc_ids_none_when_unscoped():
    assert review_catalog_doc_ids({}) is None


def test_filter_catalog_entries():
    entries = [_entry("a"), _entry("b"), _entry("c")]
    filtered = filter_catalog_entries(entries, {"a", "c"})
    assert [e.document_id for e in filtered] == ["a", "c"]
