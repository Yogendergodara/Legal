"""Pydantic request and response models."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

SearchType = Literal["web", "internal", "all"]
SourceType = Literal["web", "internal"]
CitationDirection = Literal["incoming", "outgoing", "both"]


class SearchRequest(BaseModel):
    query: str
    search_type: SearchType = "all"
    jurisdiction: str = "India"
    max_results: int = Field(default=10, ge=1, le=100)
    tenant_id: str | None = None
    filters: dict[str, Any] | None = None


class SearchResult(BaseModel):
    source_id: str
    source_type: SourceType
    title: str
    text_snippet: str
    url: str
    jurisdiction: str
    relevance_score: float = Field(ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    request_id: str
    query: str
    search_type: SearchType
    results: list[SearchResult]
    total_results: int
    degraded: bool = False
    search_time_ms: int


class FetchRequest(BaseModel):
    source_id: str
    source_type: SourceType
    extract_sections: list[str] | None = None
    tenant_id: str | None = None


class ExtractedSection(BaseModel):
    section_id: str
    title: str
    content: str


class FetchResponse(BaseModel):
    request_id: str
    source_id: str
    source_type: SourceType
    title: str
    full_text: str
    sections: list[ExtractedSection]
    url: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    fetch_time_ms: int


class SemanticSearchRequest(BaseModel):
    query: str
    search_type: SearchType = "all"
    top_k: int = Field(default=10, ge=1, le=100)
    threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    tenant_id: str | None = None


class SemanticSearchResult(BaseModel):
    source_id: str
    source_type: SourceType
    title: str
    text_snippet: str
    similarity_score: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class SemanticSearchResponse(BaseModel):
    request_id: str
    query: str
    results: list[SemanticSearchResult]
    total_results: int
    search_time_ms: int
    stub: bool = False
    stub_reason: str | None = None


class IngestInternalRequest(BaseModel):
    tenant_id: str
    title: str
    text: str
    source_id: str | None = None
    metadata: dict[str, Any] | None = None


class IngestInternalResponse(BaseModel):
    request_id: str
    tenant_id: str
    source_id: str
    title: str
    deduped: bool = False
    ingest_time_ms: int


class CitationGraphRequest(BaseModel):
    source_id: str
    source_type: SourceType
    depth: int = Field(default=1, ge=1, le=5)
    direction: CitationDirection = "both"


class CitationNode(BaseModel):
    source_id: str
    source_type: SourceType
    title: str
    url: str


class CitationEdge(BaseModel):
    from_id: str
    to_id: str
    citation_type: str


class CitationGraphResponse(BaseModel):
    request_id: str
    source_id: str
    nodes: list[CitationNode]
    edges: list[CitationEdge]
    depth: int
    direction: CitationDirection
    stub: bool = False
    stub_reason: str | None = None
    graph_time_ms: int


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    timestamp: datetime


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
