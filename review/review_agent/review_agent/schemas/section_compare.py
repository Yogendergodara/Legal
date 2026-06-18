"""Structured LLM output for section-first compliance compare."""

from __future__ import annotations

from pydantic import BaseModel, Field

from document_core.schemas.compliance import ComplianceStatus, Severity


class SectionCompareItem(BaseModel):
    section_id: str
    policy_document_id: str = ""
    policy_section_id: str = ""
    dimension_label: str = ""
    status: ComplianceStatus
    severity: Severity = Severity.INFO
    contract_quote: str = ""
    policy_quote: str = ""
    rationale: str = Field(..., min_length=5)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class BatchSectionCompareLLMResult(BaseModel):
    items: list[SectionCompareItem] = Field(default_factory=list)
