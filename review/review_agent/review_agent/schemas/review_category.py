"""Dynamic review category derived from indexed policy sections."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class ReviewCategory(BaseModel):
    """One compliance review unit — typically one policy parent section."""

    category_id: str
    label: str
    policy_document_id: UUID | None = None
    policy_section_id: str = ""
    search_queries: list[str] = Field(default_factory=list)
    review_guidance: str = ""
    source: str = "policy_section"
