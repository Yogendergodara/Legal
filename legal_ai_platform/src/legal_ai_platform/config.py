"""Platform configuration loaded from environment variables."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class PlatformSettings(BaseSettings):
    """Runtime settings for the Legal AI Platform."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    retrieval_server_url: str = "http://localhost:8001"
    retrieval_timeout_seconds: float = 30.0
    retrieval_max_retries: int = 3
    legal_search_backend: str = "custom"
    # Upper bound on a single agent run (the full pipeline can be long). 0 = no limit.
    agent_timeout_seconds: float = 300.0
    platform_host: str = "0.0.0.0"
    platform_port: int = 8080
    platform_log_level: str = "INFO"


@lru_cache
def get_settings() -> PlatformSettings:
    """Return cached platform settings."""
    return PlatformSettings()
