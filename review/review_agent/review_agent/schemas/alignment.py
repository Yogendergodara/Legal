"""Policy–contract alignment record for hybrid compliance."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class AlignmentRecord(BaseModel):
    """Retrieved pair aligned for batch compliance (excerpts truncated for prompts)."""

    category_id: str
    policy_document_id: UUID | None = None
    policy_section_id: str | None = None
    policy_hit_score: float = 0.0
    contract_hit_score: float = 0.0
    combined_score: float = 0.0
    policy_text_excerpt: str = ""
    contract_text_excerpt: str = ""
    retrieval_method: str = ""
