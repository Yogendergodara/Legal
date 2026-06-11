"""Unified retrieval result schema consumed by all agents."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RetrievalResult(BaseModel):
    """Normalized result from any retrieval MCP tool."""

    source: str = Field(description="Source identifier (e.g. web URL, internal doc id)")
    title: str = ""
    url: str = ""
    content: str = Field(description="Text snippet or excerpt")
    citation: str = Field(default="", description="Legal citation if available")
    score: float = Field(
        default=0.0,
        description="Relevance/similarity score; not bounded (semantic scores may exceed 1.0)",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_search_hit(cls, hit: dict[str, Any]) -> RetrievalResult:
        """Map a retrieval-server SearchResult dict to RetrievalResult."""
        metadata = hit.get("metadata") or {}
        source_type = hit.get("source_type", "web")
        source_id = hit.get("source_id", "")
        citation = metadata.get("citation") or metadata.get("citation_text") or hit.get("title", "")
        raw_score = hit.get("relevance_score")
        if raw_score is None:
            raw_score = hit.get("similarity_score", 0.0)
        return cls(
            source=f"{source_type}:{source_id}" if source_id else source_type,
            title=hit.get("title", ""),
            url=hit.get("url", ""),
            content=hit.get("text_snippet", ""),
            citation=citation,
            score=float(raw_score),
            metadata={
                **metadata,
                "source_id": source_id,
                "source_type": source_type,
                "jurisdiction": hit.get("jurisdiction", ""),
            },
        )


class FetchResult(BaseModel):
    """Normalized full-document fetch result."""

    source_id: str
    source_type: str
    title: str
    full_text: str
    url: str = ""
    sections: list[dict[str, str]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    fetch_time_ms: int = 0


class CitationGraphResult(BaseModel):
    """Normalized citation graph result."""

    source_id: str
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)
    depth: int = 1
    direction: str = "both"
    stub: bool = False
    stub_reason: str | None = None
    graph_time_ms: int = 0
