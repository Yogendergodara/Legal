"""LangGraph state for the section-first compliance review pipeline."""

from __future__ import annotations

import operator
from typing import Annotated, Any

from typing_extensions import TypedDict

from document_core.schemas.chunk import IngestResult, IndexedChunk
from document_core.schemas.compliance import ComplianceFinding, ReviewReport


def merge_warnings(existing: list[str], new: list[str]) -> list[str]:
    if not new:
        return existing
    seen = set(existing)
    merged = list(existing)
    for item in new:
        if item not in seen:
            seen.add(item)
            merged.append(item)
    return merged


class ReviewState(TypedDict, total=False):
    tenant_id: str
    contract_title: str
    contract_document_id: str
    contract_text: str | None
    contract_type: str | None
    policy_type: str | None

    ingest_result: IngestResult
    contract_sections: list[IndexedChunk]
    indexed_policies: list[dict[str, Any]]
    policy_document_ids: list[str]
    contract_routing: dict[str, Any]
    discovered_policies: list[dict[str, Any]]
    discovered_policy_document_ids: list[str]
    discovery_warnings: list[str]

    section_context_by_id: dict[str, dict[str, Any]]

    section_retrieval_by_id: dict[str, dict[str, Any]]
    section_review_sections: list[dict[str, Any]]
    section_compare_items: list[dict[str, Any]]
    gap_section_ids: list[str]
    no_policy_gap_ids: list[str]
    compare_omitted_gap_ids: list[str]
    unclear_finding_ids: list[str]
    unclear_recompare_finding_ids: list[str]
    conflict_pairs: list[list[str]]
    final_verify_stats: dict[str, Any]
    section_coverage: dict[str, Any]
    compliance_stats: dict[str, Any]
    superseded_finding_ids: list[str]

    obligations: list[dict[str, Any]]
    obligation_extract_stats: dict[str, Any]
    obligation_routing_by_id: dict[str, dict[str, Any]]
    obligation_catalog_match_by_id: dict[str, dict[str, Any]]
    obligation_routing_candidate_doc_ids: list[str]
    obligation_retrieval_by_id: dict[str, dict[str, Any]]
    obligation_evidence_by_id: dict[str, dict[str, Any]]
    obligation_compare_items: list[dict[str, Any]]
    obligation_findings: list[dict[str, Any]]

    findings: list[ComplianceFinding]
    grounded_findings: list[ComplianceFinding]
    warnings: Annotated[list[str], merge_warnings]
    failed_sections: Annotated[list[dict[str, Any]], operator.add]
    report: ReviewReport

    thread_id: str
    memory_context: str
    memory_hits: list[dict[str, Any]]
    memory_saved: bool
    memory_save_message: str
