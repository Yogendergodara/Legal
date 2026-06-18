"""Gap retrieval request when policy text is missing after Pass 1."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class GapRequest(BaseModel):
    """One missing-policy gap to resolve before Pass 2."""

    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    category_id: str | None = None
    policy_topic: str = ""
    contract_quote: str = ""
    suggested_search_queries: list[str] = Field(default_factory=list)
