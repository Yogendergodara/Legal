"""Structured LLM output for optional policy plan category filter."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PolicyPlanFilterResult(BaseModel):
    """Subset of review category IDs relevant to the contract under review."""

    relevant_category_ids: list[str] = Field(default_factory=list)
    search_query_overrides: dict[str, list[str]] = Field(default_factory=dict)
    rationale: str = ""
