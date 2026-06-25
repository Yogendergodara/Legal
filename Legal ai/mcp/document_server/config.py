"""Runtime configuration for the Document MCP server."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

SERVICE_NAME = "document-mcp"
VERSION = "0.1.0"

MCP_CAPABILITIES = [
    "search_request_metadata",
    "search_policy_by_categories",
    "search_policy_catalog",
    "structured_sections_ingest",
    "verify_quote",
]


def resolve_build_id() -> str:
    """Build id for /health — env override, git short SHA, or dev timestamp."""
    import os
    from datetime import datetime, timezone

    explicit = (os.environ.get("DOCUMENT_MCP_BUILD_ID") or "").strip()
    if explicit:
        return explicit

    import subprocess

    for repo in (
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        os.getcwd(),
    ):
        try:
            result = subprocess.run(
                ["git", "-C", repo, "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                check=False,
                timeout=2,
            )
            sha = (result.stdout or "").strip()
            if result.returncode == 0 and sha:
                return sha
        except (OSError, subprocess.SubprocessError):
            continue

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{VERSION}-dev-{stamp}"


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


@lru_cache
def get_settings() -> Settings:
    return Settings()
