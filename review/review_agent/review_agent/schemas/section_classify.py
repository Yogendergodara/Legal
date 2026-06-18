"""Section → policy category classification (Phase 10)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SectionCategoryResult(BaseModel):
    section_id: str
    categories: list[str] = Field(default_factory=list)
    query_terms: list[str] = Field(default_factory=list)


class SectionCategoryLLMResult(BaseModel):
    categories: list[str] = Field(default_factory=list)
    query_terms: list[str] = Field(default_factory=list)
