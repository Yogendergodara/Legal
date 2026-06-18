"""Structured output for contract routing (topic discovery for policy retrieval)."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator


class ContractRoutingResult(BaseModel):
    """LLM or lexical routing: which playbook topics to search in the tenant index."""

    contract_type: str = "unknown"
    topics: list[str] = Field(default_factory=list, max_length=20)
    section_titles: list[str] = Field(default_factory=list, max_length=50)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @field_validator("contract_type", mode="before")
    @classmethod
    def normalize_contract_type(cls, value: object) -> str:
        text = str(value or "").strip().lower()
        return text or "unknown"

    @field_validator("topics", "section_titles", mode="before")
    @classmethod
    def coerce_str_list(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            return [str(value).strip()] if str(value).strip() else []
        return [str(item).strip() for item in value if str(item).strip()]

    @model_validator(mode="after")
    def dedupe_topics(self) -> ContractRoutingResult:
        seen: set[str] = set()
        unique: list[str] = []
        for topic in self.topics:
            key = topic.lower()
            if key not in seen:
                seen.add(key)
                unique.append(topic)
        object.__setattr__(self, "topics", unique[:20])
        if not self.topics:
            raise ValueError("topics must contain at least one non-empty phrase")
        return self
