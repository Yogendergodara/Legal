"""Obligation routing and catalog match schemas (Phase R2/R3)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ObligationRoutingPlan(BaseModel):
    obligation_id: str
    intent: str = ""
    concepts: list[str] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)
    explicit_policy_mentions: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reasoning: str = ""
    routing_source: Literal[
        "registry_alias", "llm", "planner_fallback", "skipped_boilerplate"
    ] = "llm"
    resolved_document_ids: list[str] = Field(default_factory=list)


class PlannerRoutingItem(BaseModel):
    obligation_id: str
    intent: str = ""
    concepts: list[str] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reasoning: str = ""


class BatchRoutingPlanResult(BaseModel):
    plans: list[PlannerRoutingItem] = Field(default_factory=list)


class CatalogMatchResult(BaseModel):
    obligation_id: str
    candidate_doc_ids: list[str] = Field(default_factory=list)
    candidate_scores: dict[str, float] = Field(default_factory=dict)
    routing_source: str = ""
    confidence: float = 0.0
    queries_used: list[str] = Field(default_factory=list)
    rejected: list[dict[str, str]] = Field(default_factory=list)
    route_decision: Literal["compare", "ipc", "expand"] = "compare"
