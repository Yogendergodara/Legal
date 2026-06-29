"""Structured LLM output for section-first compliance compare."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from document_core.schemas.compliance import ComplianceStatus, Severity
from review_agent.schemas.compliance_status_utils import normalize_compliance_status
from review_agent.schemas.quote_field_utils import coerce_optional_str, coerce_quote_field


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

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: object) -> object:
        return normalize_compliance_status(value)

    @field_validator("contract_quote", "policy_quote", mode="before")
    @classmethod
    def coerce_quotes(cls, value: object) -> str:
        return coerce_quote_field(value)

    @field_validator("policy_document_id", "policy_section_id", "section_id", mode="before")
    @classmethod
    def coerce_ids(cls, value: object) -> str:
        return coerce_optional_str(value)


class BatchSectionCompareLLMResult(BaseModel):
    items: list[SectionCompareItem] = Field(default_factory=list)


class FinalGapVerifyItem(BaseModel):
    section_id: str
    status: ComplianceStatus
    severity: Severity = Severity.INFO
    contract_quote: str = ""
    rationale: str = Field(..., min_length=5)

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: object) -> object:
        return normalize_compliance_status(value)

    @field_validator("contract_quote", mode="before")
    @classmethod
    def coerce_contract_quote(cls, value: object) -> str:
        return coerce_quote_field(value)


class BatchFinalGapVerifyLLMResult(BaseModel):
    items: list[FinalGapVerifyItem] = Field(default_factory=list)
