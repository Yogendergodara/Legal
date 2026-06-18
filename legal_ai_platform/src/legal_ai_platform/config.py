"""Platform configuration loaded from environment variables."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# Always resolve legal_ai_platform/.env regardless of process working directory.
_ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"


class PlatformSettings(BaseSettings):
    """Runtime settings for the Legal AI Platform."""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    retrieval_server_url: str = "http://localhost:8001"
    retrieval_timeout_seconds: float = 30.0
    retrieval_max_retries: int = 3
    document_server_url: str = "http://localhost:8003"
    document_timeout_seconds: float = 60.0
    document_max_retries: int = 3
    legal_search_backend: str = "custom"
    # Upper bound on a single agent run (the full pipeline can be long). 0 = no limit.
    agent_timeout_seconds: float = 300.0
    platform_host: str = "0.0.0.0"
    platform_port: int = 8080
    platform_log_level: str = "INFO"
    platform_session_dir: str = "memory/sessions"
    session_store_backend: Literal["file", "postgres"] = "file"
    database_url: str | None = None
    session_transcript_load_limit: int = 500
    session_transcript_max_turns: int = 20
    memory_store_backend: Literal["mcp", "postgres"] = "mcp"
    platform_owns_long_term_memory: bool = True
    platform_owns_session: bool = True
    session_memory_max_hits: int = 5
    session_delete_legacy_research_files: bool = True


@lru_cache
def get_settings() -> PlatformSettings:
    """Return cached platform settings."""
    return PlatformSettings()
