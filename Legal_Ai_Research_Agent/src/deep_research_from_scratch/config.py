"""Centralized runtime configuration.

Single source of truth for tunable knobs (limits, thresholds, backend
selectors). Every value can be overridden via an environment variable, so
deployments adjust behavior without code changes. Modules read these instead of
hardcoding constants, which prevents drift and hardcoded-value bugs.

    from deep_research_from_scratch.config import config
    if len(messages) > config.COMPACT_THRESHOLD: ...

Note: dynamic filesystem paths (e.g. the per-session memory dir) are still
resolved at call time in ``memory_tools`` so they can change per request; only
stable numeric/selector knobs live here.
"""

from __future__ import annotations

import os
from pathlib import Path


def _load_platform_env() -> None:
    """Load ``legal_ai_platform/.env`` when this package is imported first.

    The platform gateway loads the same file from ``legal_ai_platform.__init__``,
    but research code is sometimes imported directly (tests, notebooks). Loading
    here is idempotent (``override=False``) and ensures fetch/supervisor knobs
  from ``.env`` are visible before ``config`` is constructed.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    repo_root = Path(__file__).resolve().parents[3]
    for env_path in (
        repo_root / "legal_ai_platform" / ".env",
        repo_root / "Legal_Ai_Research_Agent" / ".env",
    ):
        if env_path.is_file():
            load_dotenv(env_path, override=False)


_load_platform_env()


def _env(name: str, default: str) -> str:
    """Read an env var, treating empty/whitespace as unset."""
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except (TypeError, ValueError):
        return default


def _bool(name: str, default: bool) -> bool:
    value = _env(name, str(default).lower())
    return value.lower() in ("true", "1", "yes")


class _Config:
    """Process-wide configuration, populated from the environment at import."""

    def __init__(self) -> None:
        # ── Long-term memory index (MEMORY.md) size caps ─────────────────────
        self.MAX_ENTRYPOINT_LINES: int = _int("DEEP_RESEARCH_MEMORY_MAX_LINES", 200)
        self.MAX_ENTRYPOINT_BYTES: int = _int("DEEP_RESEARCH_MEMORY_MAX_BYTES", 25_000)

        # ── Conversation compaction (agent loops) ────────────────────────────
        self.COMPACT_THRESHOLD: int = _int("DEEP_RESEARCH_COMPACT_THRESHOLD", 12)
        self.KEEP_RECENT_MESSAGES: int = _int("DEEP_RESEARCH_KEEP_RECENT", 6)

        # ── Rolling per-session summary ──────────────────────────────────────
        self.SESSION_SUMMARY_THRESHOLD: int = _int("DEEP_RESEARCH_SESSION_SUMMARY_THRESHOLD", 12)
        self.SESSION_KEEP_RECENT: int = _int("DEEP_RESEARCH_SESSION_KEEP_RECENT", 6)

        # ── Supervisor / research loop limits ────────────────────────────────
        self.MAX_RESEARCHER_ITERATIONS: int = _int("MAX_RESEARCHER_ITERATIONS", 15)
        self.MAX_CONCURRENT_RESEARCHERS: int = _int("MAX_CONCURRENT_RESEARCHERS", 3)

        # ── Report verification gate ─────────────────────────────────────────
        self.MAX_REVIEWER_RETRIES: int = _int("MAX_REVIEWER_RETRIES", 3)

        # ── Fetch discipline (researcher subgraph) ────────────────────────────
        # Ensures the memo has enough cited cases (SC + HC mix) before finishing.
        self.MIN_FETCHES: int = _int("MIN_FETCHES", 10)
        self.MIN_PRIMARY_FETCHES: int = _int("MIN_PRIMARY_FETCHES", 8)
        self.MIN_SEARCHES: int = _int("MIN_SEARCHES", 6)
        self.FETCH_MAX_CHARS: int = _int("FETCH_MAX_CHARS", 24000)
        self.MAX_FETCH_GATE_RETRIES: int = _int("MAX_FETCH_GATE_RETRIES", 8)

        # ── Deterministic research bootstrap (pre-supervisor) ──────────────────
        self.ENABLE_RESEARCH_BOOTSTRAP: bool = _bool("ENABLE_RESEARCH_BOOTSTRAP", True)
        self.BOOTSTRAP_SEARCH_QUERIES: int = _int("BOOTSTRAP_SEARCH_QUERIES", 8)
        self.BOOTSTRAP_MAX_FETCHES: int = _int("BOOTSTRAP_MAX_FETCHES", 8)
        self.BOOTSTRAP_RESULTS_PER_QUERY: int = _int("BOOTSTRAP_RESULTS_PER_QUERY", 10)
        self.BOOTSTRAP_MIN_TARGET_FETCHES: int = _int("BOOTSTRAP_MIN_TARGET_FETCHES", 6)

        # ── Fast research (skip LLM supervisor when bootstrap has sources) ───
        self.FAST_RESEARCH_MODE: bool = _bool("FAST_RESEARCH_MODE", False)
        # Default matches bootstrap target so a successful bootstrap can skip
        # the supervisor without a separate FAST_MODE_MIN_FETCHES override.
        self.FAST_MODE_MIN_FETCHES: int = _int(
            "FAST_MODE_MIN_FETCHES",
            self.BOOTSTRAP_MIN_TARGET_FETCHES,
        )

        # ── Clarification gate (scoping phase) ─────────────────────────────────
        # When true (default), the LLM may ask ONE targeted follow-up question
        # before starting research to improve direction and result quality.
        self.ALLOW_CLARIFICATION: bool = _bool("ALLOW_CLARIFICATION", True)

        # ── Legal ai Retrieval MCP server ────────────────────────────────────
        self.RETRIEVAL_SERVER_URL: str = _env("RETRIEVAL_SERVER_URL", "http://localhost:8001")
        self.RETRIEVAL_TIMEOUT_SECONDS: float = float(
            _env("RETRIEVAL_TIMEOUT_SECONDS", "30")
        )
        self.RETRIEVAL_MAX_RETRIES: int = _int("RETRIEVAL_MAX_RETRIES", 3)

        # ── Pluggable backends ───────────────────────────────────────────────
        self.MEMORY_BACKEND: str = _env("MEMORY_BACKEND", "file").lower()

        # ── Input validation ─────────────────────────────────────────────────
        # Hard cap on a single user message (chars) used to reject pathological
        # input before it reaches the LLM graph.
        self.MAX_INPUT_CHARS: int = _int("DEEP_RESEARCH_MAX_INPUT_CHARS", 100_000)

        # ── LLM context budgeting (mirrors LLM_* env vars in model_config) ───
        # Max chars of research findings passed to the memo writer before truncation.
        self.LLM_FINDINGS_CHAR_BUDGET: int = _int("LLM_FINDINGS_CHAR_BUDGET", 60_000)

        # Skip the LLM semantic verifier (use deterministic checks only).
        # Recommended on Mistral free tier to avoid extra API calls / 429 errors.
        self.LLM_SKIP_VERIFIER: bool = _bool("LLM_SKIP_VERIFIER", False)

        # ── Normal Research mode (lightweight) ────────────────────────────────
        # Max search queries issued by the normal researcher loop (2-3 rounds).
        self.NORMAL_MAX_SEARCH_QUERIES: int = _int("NORMAL_MAX_SEARCH_QUERIES", 3)
        # Max document fetches in the normal researcher loop.
        self.NORMAL_MAX_FETCHES: int = _int("NORMAL_MAX_FETCHES", 4)
        # Results requested per search query in normal mode.
        self.NORMAL_RESULTS_PER_QUERY: int = _int("NORMAL_RESULTS_PER_QUERY", 5)
        # Max chars of retrieved text passed to the normal answer writer.
        self.NORMAL_FINDINGS_CHAR_BUDGET: int = _int("NORMAL_FINDINGS_CHAR_BUDGET", 20_000)

        if self.FAST_RESEARCH_MODE:
            # Cap only unset knobs — explicit MIN_FETCHES in .env is always honored.
            if os.environ.get("MIN_FETCHES") is None:
                self.MIN_FETCHES = min(self.MIN_FETCHES, 4)
            if os.environ.get("MIN_PRIMARY_FETCHES") is None:
                self.MIN_PRIMARY_FETCHES = min(self.MIN_PRIMARY_FETCHES, 2)
            if os.environ.get("MIN_SEARCHES") is None:
                self.MIN_SEARCHES = min(self.MIN_SEARCHES, 4)
            if os.environ.get("MAX_FETCH_GATE_RETRIES") is None:
                self.MAX_FETCH_GATE_RETRIES = min(self.MAX_FETCH_GATE_RETRIES, 3)


config = _Config()
