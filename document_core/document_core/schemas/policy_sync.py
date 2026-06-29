"""Schemas for batch policy sync (Java → document-mcp)."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class PolicySyncInput(BaseModel):
    document_id: UUID | None = None
    policy_ref: str | None = None
    title: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1)


class SyncPoliciesRequest(BaseModel):
    tenant_id: str = Field(..., min_length=1)
    policies: list[PolicySyncInput] = Field(..., min_length=1)
    replace_policies: bool = False
    source: str = "java-sync"


class PolicySyncResult(BaseModel):
    kind: Literal["policy"] = "policy"
    policy_ref: str
    document_id: str
    title: str
    index_status_after: Literal["indexed"] = "indexed"
    parent_count: int
    structure_confidence: str
    categories: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    skipped: bool = False
    auto_tagged: bool = True
    tagger: str = "llm"


class SyncPoliciesPreflight(BaseModel):
    policies_synced: int
    tombstoned_count: int
    duplicate_primary_categories: list[str] = Field(default_factory=list)
    weak_tag_count: int = 0
    weak_tag_policies: list[str] = Field(default_factory=list)


class SyncPoliciesResponse(BaseModel):
    tenant_id: str
    policies: list[PolicySyncResult]
    tombstoned_policy_refs: list[str] = Field(default_factory=list)
    preflight: SyncPoliciesPreflight
