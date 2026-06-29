"""Structured LLM batch output for policy section category tagging."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SectionCategoryTag(BaseModel):
    section_id: str = Field(..., min_length=1)
    categories: list[str] = Field(
        default_factory=list,
        description="1-5 taxonomy labels from allowed set",
    )


class BatchSectionCategoryTagResult(BaseModel):
    items: list[SectionCategoryTag] = Field(
        default_factory=list,
        description="One entry per section in the batch",
    )
