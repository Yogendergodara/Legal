"""LangGraph state for the compliance review pipeline."""

from __future__ import annotations

import operator
from typing import Annotated, Any
from uuid import UUID

from typing_extensions import TypedDict

from document_core.schemas.chunk import IngestResult, IndexedChunk, RetrievalHit
from document_core.schemas.compliance import ComplianceFinding, ReviewReport


class ReviewState(TypedDict, total=False):
    tenant_id: str
    contract_text: str
    contract_title: str
    policy_texts: list[dict[str, Any]]
    contract_type: str | None
    policy_type: str | None

    ingest_result: IngestResult
    contract_sections: list[IndexedChunk]
    indexed_policies: list[dict[str, Any]]
    review_categories: list[dict[str, Any]]
    policy_hits_by_category: dict[str, list[RetrievalHit]]
    contract_hits_by_category: dict[str, list[RetrievalHit]]
    policy_document_ids: list[str]
    contract_routing: dict[str, Any]
    discovered_policies: list[dict[str, Any]]
    discovered_policy_document_ids: list[str]
    discovery_warnings: list[str]
    policy_refs: list[str]
    fetched_policy_refs: list[str]
    policy_ref_by_document_id: dict[str, str]
    retrieval_meta_by_category: dict[str, dict[str, Any]]
    alignment_by_category: dict[str, dict[str, Any]]
    prescreen_findings: list[ComplianceFinding]
    deferred_category_ids: list[str]
    pass1_findings: list[ComplianceFinding]
    pass2_findings: list[ComplianceFinding]
    gap_requests: list[dict[str, Any]]
    gap_hits_by_request: dict[str, list[RetrievalHit]]
    compliance_stats: dict[str, Any]
    findings: Annotated[list[ComplianceFinding], operator.add]
    grounded_findings: list[ComplianceFinding]
    warnings: Annotated[list[str], operator.add]
    report: ReviewReport

    thread_id: str
    memory_context: str
    memory_hits: list[dict[str, Any]]
    memory_saved: bool
    memory_save_message: str

    # Phase 10 section-first
    section_retrieval_by_id: dict[str, dict[str, Any]]
    section_review_sections: list[dict[str, Any]]
    section_compare_items: list[dict[str, Any]]
