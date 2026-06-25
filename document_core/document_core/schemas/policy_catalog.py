"""Policy catalog profile and search schemas (Phase R0)."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class PolicyCatalogProfile(BaseModel):
    summary: str = ""
    topics: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    obligation_types: list[str] = Field(default_factory=list)
    profile_text: str = ""
    catalog_version: int = 1
    profiler: Literal["llm", "keyword", "off"] = "off"
    profiled_at: str = ""

    def with_profile_text(self, *, title: str) -> PolicyCatalogProfile:
        parts = [title.strip(), self.summary.strip()]
        parts.extend(self.topics)
        parts.extend(self.keywords)
        text = ". ".join(p for p in parts if p)
        return self.model_copy(update={"profile_text": text})


class PolicyProfilerLLMResult(BaseModel):
    summary: str = ""
    topics: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    obligation_types: list[str] = Field(default_factory=list)


class CatalogSearchRequest(BaseModel):
    tenant_id: str
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=10, ge=1, le=50)
    document_ids: list[UUID] | None = None


class CatalogSearchHit(BaseModel):
    document_id: UUID
    policy_ref: str = ""
    title: str = ""
    score: float = 0.0
    summary: str = ""
