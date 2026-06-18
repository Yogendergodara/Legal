"""Per-section high-recall policy retrieval bundle."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from document_core.schemas.chunk import RetrievalHit


class SectionRetrievalBundle(BaseModel):
    section_id: str
    categories: list[str] = Field(default_factory=list)
    policy_hits: list[RetrievalHit] = Field(default_factory=list)
    retrieval_meta: dict[str, Any] = Field(default_factory=dict)
