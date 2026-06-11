"""Research-specific request/response models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from legal_ai_platform.models.retrieval import RetrievalResult


class ResearchRequest(BaseModel):
    """Input for the Research Agent."""

    query: str
    context: dict[str, Any] = Field(default_factory=dict)
    tenant_id: str | None = None
    max_results: int = Field(default=10, ge=1, le=100)
    thread_id: str | None = None


class ResearchResponse(BaseModel):
    """Output from the Research Agent."""

    report: str = ""
    research_brief: str | None = None
    sources: list[RetrievalResult] = Field(default_factory=list)
    raw_notes: list[str] = Field(default_factory=list)
    verification: dict[str, Any] | None = None
    awaiting_input: bool = False
