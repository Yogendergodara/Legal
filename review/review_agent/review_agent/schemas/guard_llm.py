"""Rationale guard structured output (P2-6 tiered)."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class SupportLevel(str, Enum):
    FULL = "FULL"
    INFERENCE_OK = "INFERENCE_OK"
    UNSUPPORTED = "UNSUPPORTED"


class RationaleGuardResult(BaseModel):
    support_level: SupportLevel
    reason: str = Field(default="", max_length=500)


class RationaleGuardBatchItem(BaseModel):
    finding_id: str
    support_level: SupportLevel
    reason: str = Field(default="", max_length=500)


class BatchRationaleGuardLLMResult(BaseModel):
    items: list[RationaleGuardBatchItem] = Field(default_factory=list)


class RationaleRepairResult(BaseModel):
    rationale: str = Field(..., min_length=5)


class RationaleRepairBatchItem(BaseModel):
    finding_id: str
    rationale: str = Field(..., min_length=5)


class BatchRationaleRepairLLMResult(BaseModel):
    items: list[RationaleRepairBatchItem] = Field(default_factory=list)
