"""Review audit artifact — reproducible pipeline trail (Sprint P5)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from review_agent.schemas.section_compare import SectionCompareItem

ARTIFACT_VERSION = "1.1"


class SectionAuditRow(BaseModel):
    section_id: str
    title: str = ""
    char_count: int = 0
    categories: list[str] = Field(default_factory=list)


class RetrievalHitRef(BaseModel):
    document_id: str
    section_id: str
    score: float = 0.0


class RetrievalAuditRow(BaseModel):
    section_id: str
    categories: list[str] = Field(default_factory=list)
    hit_count: int = 0
    hits: list[RetrievalHitRef] = Field(default_factory=list)
    retrieval_meta: dict[str, Any] = Field(default_factory=dict)


class GapLlmAuditRow(BaseModel):
    section_id: str
    finding_id: str
    status: str
    rationale_preview: str = ""


class ObligationRoutingAuditRow(BaseModel):
    obligation_id: str
    section_id: str
    routing_source: str = ""
    confidence: float = 0.0
    candidate_doc_ids: list[str] = Field(default_factory=list)
    candidate_titles: list[str] = Field(default_factory=list)
    evidence_decision: str = ""
    evidence_reason: str = ""
    queries_used: list[str] = Field(default_factory=list)
    hit_count: int = 0


class ReviewArtifactOps(BaseModel):
    retrieval_retry_sections: int = 0
    retrieval_max_attempts_used: int = 0
    retrieval_zero_hit_sections: int = 0
    llm_batches_failed: int = 0
    gap_llm_sections: int = 0
    gap_llm_failed: int = 0
    unclear_recompared: int = 0
    conflicts_recompared: int = 0
    conflicts_unresolved: int = 0
    superseded_count: int = 0
    ungrounded_count: int = 0
    grounding_downgraded_count: int = 0
    backfill_count: int = 0
    post_grounding_backfill_count: int = 0
    playbook_compare_count: int = 0
    policy_conflict_count: int = 0
    guard_checked: int = 0
    guard_failed: int = 0
    quote_repair_attempts: int = 0
    quote_repair_success: int = 0
    guard_inference_ok: int = 0
    guard_repair_attempts: int = 0
    guard_repair_success: int = 0
    reranker_cross_encoder_sections: int = 0
    reranker_lexical_fallback_sections: int = 0
    degraded_section_count: int = 0
    retrieval_zero_hit_section_ids: list[str] = Field(default_factory=list)


class ReviewArtifact(BaseModel):
    artifact_version: str = ARTIFACT_VERSION
    run_id: str = ""
    pipeline: str = "section_first"
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    tenant_id: str = ""
    contract_document_id: str = ""
    contract_title: str = ""

    sections: list[SectionAuditRow] = Field(default_factory=list)
    routing: dict[str, Any] = Field(default_factory=dict)
    discovery: dict[str, Any] = Field(default_factory=dict)

    retrieval: list[RetrievalAuditRow] = Field(default_factory=list)
    compare_items: list[SectionCompareItem] = Field(default_factory=list)
    work_queue: dict[str, Any] = Field(default_factory=dict)

    gap_llm: list[GapLlmAuditRow] = Field(default_factory=list)
    obligation_routing: list[ObligationRoutingAuditRow] = Field(default_factory=list)
    superseded_finding_ids: list[str] = Field(default_factory=list)

    final_verify_stats: dict[str, Any] = Field(default_factory=dict)
    section_coverage: dict[str, Any] = Field(default_factory=dict)
    compliance_stats: dict[str, Any] = Field(default_factory=dict)
    degraded_sections: list[dict[str, Any]] = Field(default_factory=list)
    ops: ReviewArtifactOps = Field(default_factory=ReviewArtifactOps)
