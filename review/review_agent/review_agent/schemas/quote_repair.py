"""LLM quote repair structured output (P2-7)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class QuoteRepairResult(BaseModel):
    repaired_quote: str = ""
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    repair_notes: str = ""


class QuoteRepairBatchItem(BaseModel):
    repair_id: str
    section_id: str = ""
    repaired_quote: str = ""
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    repair_notes: str = ""


class BatchQuoteRepairLLMResult(BaseModel):
    items: list[QuoteRepairBatchItem] = Field(default_factory=list)
