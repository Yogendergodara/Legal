"""Tests for configuration parsing, default values, and environment variable overrides."""

import importlib

import deep_research_from_scratch.config
import deep_research_from_scratch.memory_tools
import deep_research_from_scratch.multi_agent_supervisor
import deep_research_from_scratch.report_verification

_PLATFORM_ENV_KEYS = (
    "MAX_RESEARCHER_ITERATIONS",
    "MAX_CONCURRENT_RESEARCHERS",
    "MAX_REVIEWER_RETRIES",
    "MIN_FETCHES",
    "MIN_PRIMARY_FETCHES",
    "MIN_SEARCHES",
    "MAX_FETCH_GATE_RETRIES",
    "FETCH_MAX_CHARS",
    "ALLOW_CLARIFICATION",
    "FAST_RESEARCH_MODE",
    "FAST_MODE_MIN_FETCHES",
    "BOOTSTRAP_MIN_TARGET_FETCHES",
    "BOOTSTRAP_SEARCH_QUERIES",
    "BOOTSTRAP_MAX_FETCHES",
    "RETRIEVAL_SERVER_URL",
    "RETRIEVAL_TIMEOUT_SECONDS",
    "RETRIEVAL_MAX_RETRIES",
)


def test_config_defaults(monkeypatch):
    """Verify code defaults without legal_ai_platform/.env overrides."""
    for key in _PLATFORM_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)

    importlib.reload(deep_research_from_scratch.config)
    config = deep_research_from_scratch.config.config

    assert config.MAX_ENTRYPOINT_LINES == 200
    assert config.MAX_ENTRYPOINT_BYTES == 25000
    assert config.COMPACT_THRESHOLD == 12
    assert config.KEEP_RECENT_MESSAGES == 6
    assert config.SESSION_SUMMARY_THRESHOLD == 12
    assert config.SESSION_KEEP_RECENT == 6
    assert config.MAX_RESEARCHER_ITERATIONS == 15
    assert config.MAX_CONCURRENT_RESEARCHERS == 3
    assert config.MAX_REVIEWER_RETRIES == 3
    assert config.MIN_FETCHES == 10
    assert config.MIN_PRIMARY_FETCHES == 8
    assert config.MIN_SEARCHES == 6
    assert config.MAX_FETCH_GATE_RETRIES == 8
    assert config.FETCH_MAX_CHARS == 24000
    assert config.ALLOW_CLARIFICATION is True
    assert config.RETRIEVAL_SERVER_URL == "http://localhost:8001"
    assert config.RETRIEVAL_TIMEOUT_SECONDS == 30.0
    assert config.RETRIEVAL_MAX_RETRIES == 3
    assert config.MEMORY_BACKEND == "file"
    assert config.MAX_INPUT_CHARS == 100000
    assert config.FAST_MODE_MIN_FETCHES == config.BOOTSTRAP_MIN_TARGET_FETCHES


def test_config_env_overrides(monkeypatch):
    """Verify environment variables override configuration and propagate via module reloads."""
    monkeypatch.setenv("DEEP_RESEARCH_COMPACT_THRESHOLD", "42")
    monkeypatch.setenv("RETRIEVAL_SERVER_URL", "http://custom:9000")
    monkeypatch.setenv("MAX_REVIEWER_RETRIES", "5")
    monkeypatch.setenv("MAX_RESEARCHER_ITERATIONS", "10")

    try:
        # Reload modules in cascade order
        importlib.reload(deep_research_from_scratch.config)
        importlib.reload(deep_research_from_scratch.memory_tools)
        importlib.reload(deep_research_from_scratch.report_verification)
        importlib.reload(deep_research_from_scratch.multi_agent_supervisor)

        # Assert config itself updated
        assert deep_research_from_scratch.config.config.COMPACT_THRESHOLD == 42
        assert deep_research_from_scratch.config.config.RETRIEVAL_SERVER_URL == "http://custom:9000"
        assert deep_research_from_scratch.config.config.MAX_REVIEWER_RETRIES == 5
        assert deep_research_from_scratch.config.config.MAX_RESEARCHER_ITERATIONS == 10

        # Assert copied constants updated in dependent modules
        assert deep_research_from_scratch.memory_tools.COMPACT_THRESHOLD == 42
        assert deep_research_from_scratch.report_verification.MAX_REVIEWER_RETRIES == 5
        assert deep_research_from_scratch.multi_agent_supervisor.max_researcher_iterations == 10

    finally:
        # Revert changes to prevent test pollution
        monkeypatch.delenv("DEEP_RESEARCH_COMPACT_THRESHOLD", raising=False)
        monkeypatch.delenv("RETRIEVAL_SERVER_URL", raising=False)
        monkeypatch.delenv("MAX_REVIEWER_RETRIES", raising=False)
        monkeypatch.delenv("MAX_RESEARCHER_ITERATIONS", raising=False)

        importlib.reload(deep_research_from_scratch.config)
        importlib.reload(deep_research_from_scratch.memory_tools)
        importlib.reload(deep_research_from_scratch.report_verification)
        importlib.reload(deep_research_from_scratch.multi_agent_supervisor)


def test_fast_mode_honors_explicit_min_fetches(monkeypatch):
    """Explicit MIN_FETCHES in .env is not overridden by FAST_RESEARCH_MODE."""
    for key in _PLATFORM_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.setenv("FAST_RESEARCH_MODE", "true")
    monkeypatch.setenv("MIN_FETCHES", "7")
    monkeypatch.setenv("MIN_PRIMARY_FETCHES", "5")

    importlib.reload(deep_research_from_scratch.config)
    config = deep_research_from_scratch.config.config

    assert config.MIN_FETCHES == 7
    assert config.MIN_PRIMARY_FETCHES == 5
