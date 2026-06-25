"""Evidence sufficiency decision schema (Phase R5)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from document_core.schemas.chunk import RetrievalHit


class EvidenceSufficiencyResult(BaseModel):
    obligation_id: str
    decision: Literal["compare", "expand", "ipc"] = "ipc"
    reason: str = ""
    hit_count: int = 0
    max_relevance_score: float = 0.0
    concept_overlap_score: float = 0.0
    candidate_doc_coverage: float = 0.0
    routing_confidence: float = 0.0
    expand_round: int = 0
    final_hits: list[RetrievalHit] = Field(default_factory=list)
