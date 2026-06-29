"""Contract obligation schemas (Phase R1)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ContractObligation(BaseModel):
    obligation_id: str
    section_id: str
    text: str
    char_start: int = 0
    char_end: int = 0
    obligation_type: str = ""
    is_boilerplate: bool = False
    explicit_policy_mentions: list[str] = Field(default_factory=list)
    extract_source: Literal["llm", "fallback", "lexical"] = "fallback"


class ObligationExtractItem(BaseModel):
    index: int = 0
    text: str = ""
    obligation_type: str = ""
    explicit_policy_mentions: list[str] = Field(default_factory=list)


class SectionObligationExtractResult(BaseModel):
    section_id: str
    obligations: list[ObligationExtractItem] = Field(default_factory=list)


class BatchObligationExtractResult(BaseModel):
    sections: list[SectionObligationExtractResult] = Field(default_factory=list)


class ObligationExtractResult(BaseModel):
    obligations: list[ContractObligation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    extract_batch_failures: int = 0
    extract_single_retries: int = 0
    extract_single_recovered: int = 0
