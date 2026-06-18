"""Runtime configuration for document_core (store + search backends)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class DocumentCoreSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    document_store_backend: Literal["pgvector"] = "pgvector"
    database_url: str | None = None
    search_backend: Literal["lexical", "hybrid"] = "lexical"
    search_hybrid_alpha: float = 0.5
    retrieval_recall_top_k: int = 20
    retrieval_final_top_k: int = 10
    reranker_enabled: bool = False
    embedding_model: str = "nomic-ai/modernbert-embed-base"
    embedding_enabled: bool = True
    embedding_dim: int = 768
    embedding_truncate_dim: int | None = None
    policy_catalog_url: str | None = None
    policy_sync_enabled: bool = True


@lru_cache
def get_settings() -> DocumentCoreSettings:
    return DocumentCoreSettings()
