"""Runtime configuration for the Document MCP server."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

SERVICE_NAME = "document-mcp"
VERSION = "0.1.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    database_url: str | None = Field(default=None, alias="DATABASE_URL")
    document_store_backend: str = Field(default="pgvector", alias="DOCUMENT_STORE_BACKEND")
    search_backend: str = Field(default="lexical", alias="SEARCH_BACKEND")
    policy_catalog_url: str | None = Field(default=None, alias="POLICY_CATALOG_URL")
    policy_sync_enabled: bool = Field(default=True, alias="POLICY_SYNC_ENABLED")


@lru_cache
def get_settings() -> Settings:
    return Settings()
