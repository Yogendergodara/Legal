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
WebSearchBackend = Literal["duckduckgo", "tavily", "open-websearch", "legal-index"]


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
    # API keys for premium search backends
    tavily_api_key: str = Field(
        default="",
        alias="TAVILY_API_KEY",
    )
    indiankanoon_api_key: str = Field(
        default="",
        alias="INDIANKANOON_API_KEY",
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
        default="nomic-ai/modernbert-embed-base",
        alias="EMBEDDING_MODEL",
    )
    embedding_truncate_dim: int | None = Field(default=None, alias="EMBEDDING_TRUNCATE_DIM")
    semantic_hybrid_alpha: float = Field(
        default=0.5,
        alias="SEMANTIC_HYBRID_ALPHA",
    )
    # Root for the long-term legal memory (MEMORY.md + linked files). Must match
    # the research agent's DEEP_RESEARCH_MEMORY_DIR so both processes share one
    # memory store (they run on the same host).
    memory_dir: str = Field(
        default="memory",
        alias="DEEP_RESEARCH_MEMORY_DIR",
    )
    # Per-call timeout for each individual DDG site-search task.
    # Kept short so a single blocked engine doesn't hold up the whole fan-out.
    legal_authority_call_timeout: float = Field(
        default=8.0,
        alias="LEGAL_AUTHORITY_CALL_TIMEOUT",
    )
    # Hard budget for the entire LegalAuthoritySearchClient.search() call.
    # After this many seconds we return whatever we have collected so far.
    legal_authority_global_timeout: float = Field(
        default=18.0,
        alias="LEGAL_AUTHORITY_GLOBAL_TIMEOUT",
    )
    # When true, /tools/search (search_type=all) skips the general web backend
    # after legal-authority fan-out — avoids a redundant DuckDuckGo round-trip.
    search_skip_redundant_web: bool = Field(
        default=True,
        alias="SEARCH_SKIP_REDUNDANT_WEB",
    )


@lru_cache
def get_settings() -> Settings:
    """Return cached settings singleton."""
    return Settings()
