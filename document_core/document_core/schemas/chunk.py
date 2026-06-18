"""Shared document chunk and ingest schemas."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

from document_core.schemas.taxonomy import normalize_categories


class DocumentKind(str, Enum):
    CONTRACT = "contract"
    POLICY = "policy"


class ChunkRole(str, Enum):
    PARENT = "parent"
    CHILD = "child"


class StructureConfidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class IngestRequest(BaseModel):
    tenant_id: str
    document_id: UUID | None = None
    title: str = "Untitled document"
    kind: DocumentKind = DocumentKind.CONTRACT
    text: str = Field(..., min_length=1, description="Raw document text (PDF/DOCX later)")
    policy_type: str | None = None
    applies_to_contract_types: list[str] = Field(default_factory=list)
    categories: list[str] = Field(
        default_factory=list,
        description="Policy family tags for metadata retrieval (Phase 10)",
    )
    effective_date: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("categories", mode="before")
    @classmethod
    def normalize_ingest_categories(cls, value: list[str] | None) -> list[str]:
        return normalize_categories(value if isinstance(value, list) else [])

    @field_validator("text")
    @classmethod
    def strip_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("text must not be empty")
        return stripped


class SectionNode(BaseModel):
    section_id: str
    section_path: str
    title: str
    level: int
    text: str
    children: list[SectionNode] = Field(default_factory=list)


class DocumentTree(BaseModel):
    document_id: UUID
    title: str
    canonical_text: str
    sections: list[SectionNode]
    structure_confidence: StructureConfidence


class IndexedChunk(BaseModel):
    chunk_id: str
    document_id: UUID
    tenant_id: str
    kind: DocumentKind
    chunk_role: ChunkRole
    parent_id: str | None = None
    section_id: str
    section_path: str
    title: str
    text: str
    context_text: str = ""
    policy_type: str | None = None
    applies_to_contract_types: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class IngestResult(BaseModel):
    document_id: UUID
    tenant_id: str
    kind: DocumentKind
    title: str
    parent_count: int
    child_count: int
    structure_confidence: StructureConfidence
    warnings: list[str] = Field(default_factory=list)
    ingested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SearchRequest(BaseModel):
    tenant_id: str
    query: str = Field(..., min_length=1)
    document_id: UUID | None = None
    document_ids: list[UUID] | None = None
    kind: DocumentKind | None = None
    policy_type: str | None = None
    contract_type: str | None = None
    top_k: int = Field(default=5, ge=1, le=50)
    return_parents_only: bool = True


class RetrievalHit(BaseModel):
    parent_chunk: IndexedChunk
    score: float
    matched_child_ids: list[str] = Field(default_factory=list)


class GetSectionRequest(BaseModel):
    tenant_id: str
    document_id: UUID
    section_id: str


class ListSectionsRequest(BaseModel):
    tenant_id: str
    document_id: UUID
    kind: DocumentKind | None = None


class GroundingCheckRequest(BaseModel):
    tenant_id: str
    document_id: UUID
    quote: str = Field(..., min_length=1)
    section_id: str | None = None


class GroundingCheckResult(BaseModel):
    grounded: bool
    quote: str
    normalized_quote: str
    match_method: str = "substring"
    section_id: str | None = None
    message: str = ""


def new_document_id() -> UUID:
    return uuid4()
