"""Scope obligation catalog routing to the review policy set (RC-03/04)."""

from __future__ import annotations

from review_agent.services.catalog_registry import CatalogEntry
from review_agent.state.review_state import ReviewState


def review_catalog_doc_ids(state: ReviewState) -> set[str] | None:
    """Document IDs that bound catalog match / semantic planner for this review."""
    scoped = [
        str(doc_id).strip()
        for doc_id in (state.get("policy_document_ids") or [])
        if str(doc_id).strip()
    ]
    if scoped:
        return set(scoped)
    discovered = [
        str(doc_id).strip()
        for doc_id in (state.get("discovered_policy_document_ids") or [])
        if str(doc_id).strip()
    ]
    return set(discovered) if discovered else None


def filter_catalog_entries(
    entries: list[CatalogEntry],
    scope: set[str],
) -> list[CatalogEntry]:
    if not scope:
        return entries
    return [entry for entry in entries if entry.document_id in scope]
