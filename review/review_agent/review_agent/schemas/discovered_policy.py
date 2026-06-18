"""Policy document discovered from tenant index by topic search."""

from __future__ import annotations

from pydantic import BaseModel, Field


class DiscoveredPolicy(BaseModel):
    document_id: str
    title: str = ""
    policy_type: str | None = None
    match_score: float = 0.0
    matched_topics: list[str] = Field(default_factory=list)
    applies_to_contract_types: list[str] = Field(default_factory=list)
