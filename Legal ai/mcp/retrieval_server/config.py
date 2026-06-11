"""Application settings loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()

SERVICE_NAME = "retrieval-mcp"
VERSION = "0.1.0"

InternalStorage = Literal["postgres", "file"]
WebSearchBackend = Literal["duckduckgo", "open-websearch", "legal-index"]


class Settings(BaseSettings):
    """Runtime configuration for the Retrieval MCP server."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    websearch_base_url: str = Field(
        default="http://open-websearch:3000",
        alias="WEBSEARCH_BASE_URL",
    )
    internal_storage: InternalStorage = Field(
        default="file",
        alias="INTERNAL_STORAGE",
    )
    internal_storage_dir: str | None = Field(
        default=None,
        alias="INTERNAL_STORAGE_DIR",
    )
    websearch_backend: WebSearchBackend = Field(
        default="duckduckgo",
        alias="WEBSEARCH_BACKEND",
    )
    page_fetch_user_agent: str | None = Field(
        default=None,
        alias="PAGE_FETCH_USER_AGENT",
    )
    external_timeout_seconds: float = Field(
        default=30.0,
        alias="EXTERNAL_TIMEOUT_SECONDS",
    )
    database_url: str = Field(
        default="postgresql://legalai:legalai@postgres:5432/legalai",
        alias="DATABASE_URL",
    )
    embedding_model: str = Field(
        default="sentence-transformers/all-MiniLM-L6-v2",
        alias="EMBEDDING_MODEL",
    )
    semantic_hybrid_alpha: float = Field(
        default=0.5,
        alias="SEMANTIC_HYBRID_ALPHA",
    )


@lru_cache
def get_settings() -> Settings:
    """Return cached settings singleton."""
    return Settings()
