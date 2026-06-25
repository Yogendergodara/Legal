"""Runtime configuration for document_core (store + search backends)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class DocumentCoreSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    document_store_backend: Literal["pgvector"] = "pgvector"
    database_url: str | None = None
    search_backend: Literal["lexical", "hybrid"] = "lexical"
    search_hybrid_alpha: float = 0.5
    retrieval_recall_top_k: int = 20
    retrieval_final_top_k: int = 10
    reranker_enabled: bool = True
    reranker_backend: Literal["lexical", "cross_encoder"] = "cross_encoder"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    reranker_max_passage_chars: int = 2000
    reranker_fusion_retrieval_weight: float = 0.10
    embedding_model: str = "nomic-ai/modernbert-embed-base"
    embedding_enabled: bool = True
    embedding_dim: int = 768
    embedding_truncate_dim: int | None = None
    policy_stale_days: int = 0
    category_tagger_enabled: bool = True
    category_tagger_mode: Literal["auto", "llm", "keyword"] = "keyword"
    category_tagger_model: str = "mistral-small-latest"
    category_tagger_batch_size: int = 8
    category_tagger_max_section_chars: int = 1200
    category_tagger_max_tags_per_section: int = 3
    category_tagger_temperature: float = 0.0
    child_chunk_max_chars: int = 700
    child_chunk_overlap_sentences: int = 2
    category_search_boost: float = 0.15

    @field_validator("embedding_truncate_dim", mode="before")
    @classmethod
    def empty_truncate_dim(cls, value: object) -> object:
        if value == "" or value is None:
            return None
        return value


@lru_cache
def get_settings() -> DocumentCoreSettings:
    return DocumentCoreSettings()
