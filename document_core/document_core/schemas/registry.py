"""Policy registry schemas (metadata catalog separate from chunk index)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class RegisterPolicyRequest(BaseModel):
    tenant_id: str
    policy_ref: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    document_id: UUID | None = None
    policy_type: str | None = None
    applies_to_contract_types: list[str] = Field(default_factory=list)
    source: str = "catalog"
    metadata: dict[str, Any] = Field(default_factory=dict)


class GetPolicyByRefRequest(BaseModel):
    tenant_id: str
    policy_ref: str = Field(..., min_length=1)


class ListPolicyRegistryRequest(BaseModel):
    tenant_id: str
    kind: Literal["contract", "policy"] | None = None
    index_status: Literal["pending", "indexed", "failed"] | None = None


class SyncPolicyFromCatalogRequest(BaseModel):
    tenant_id: str
    policy_ref: str = Field(..., min_length=1)
    force_reindex: bool = False


class PolicyRegistryRecord(BaseModel):
    tenant_id: str
    document_id: UUID
    policy_ref: str
    title: str
    kind: Literal["contract", "policy"] = "policy"
    policy_type: str | None = None
    applies_to_contract_types: list[str] = Field(default_factory=list)
    index_status: Literal["pending", "indexed", "failed"]
    content_hash: str | None = None
    source: str = "catalog"
    metadata: dict[str, Any] = Field(default_factory=dict)
    indexed_at: datetime | None = None


class ListPolicyRegistryResponse(BaseModel):
    tenant_id: str
    policies: list[PolicyRegistryRecord]
