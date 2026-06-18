"""Filter policy documents by category metadata."""

from __future__ import annotations

from pydantic import BaseModel, Field

from document_core.schemas.chunk import DocumentKind
from document_core.schemas.taxonomy import normalize_categories


class CategoryFilterRequest(BaseModel):
    tenant_id: str
    categories: list[str] = Field(..., min_length=1)
    kind: DocumentKind = DocumentKind.POLICY
    contract_type: str | None = None

    @property
    def normalized_categories(self) -> list[str]:
        return normalize_categories(self.categories)
