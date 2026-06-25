"""Obligation-scoped retrieval bundle (Phase R4)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from document_core.schemas.chunk import RetrievalHit


class ObligationRetrievalBundle(BaseModel):
    obligation_id: str
    section_id: str
    candidate_doc_ids: list[str] = Field(default_factory=list)
    policy_hits: list[RetrievalHit] = Field(default_factory=list)
    queries_used: list[str] = Field(default_factory=list)
    concepts: list[str] = Field(default_factory=list)
    retrieval_meta: dict[str, Any] = Field(default_factory=dict)
    skipped_reason: str | None = None
