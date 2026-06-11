"""Central structured logging configuration for the Retrieval MCP server."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

try:
    import structlog
    from structlog.contextvars import bind_contextvars, clear_contextvars

    STRUCTLOG_AVAILABLE = True
except ImportError:
    STRUCTLOG_AVAILABLE = False
    bind_contextvars = None  # type: ignore[assignment, misc]
    clear_contextvars = None  # type: ignore[assignment, misc]

LOG_DIR = Path("logs")
LOG_FILE = LOG_DIR / "retrieval_mcp.log"
MAX_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 5

_configured = False


def truncate(text: str | None, max_len: int = 200) -> str:
    """Truncate text for safe logging (queries, snippets)."""
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _ensure_log_dir() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def _human_renderer(logger: Any, method_name: str, event_dict: dict[str, Any]) -> str:
    """Render log events as human-readable lines for the rotating file."""
    ts = event_dict.pop("timestamp", "")
    level = event_dict.pop("level", "info")
    logger_name = event_dict.pop("logger", event_dict.pop("logger_name", ""))
    event = event_dict.pop("event", event_dict.pop("message", ""))
    extras = " ".join(f"{k}={v}" for k, v in event_dict.items())
    parts = [str(ts), str(level).upper(), str(logger_name), str(event)]
    if extras:
        parts.append(extras)
    return " ".join(parts)


def configure_logging(log_level: str = "INFO") -> None:
    """Configure structured logging once at application startup."""
    global _configured
    if _configured:
        return

    _ensure_log_dir()
    level = getattr(logging, log_level.upper(), logging.INFO)

    if STRUCTLOG_AVAILABLE:
        _configure_structlog(level)
    else:
        _configure_stdlib(level)

    _configured = True


def _configure_structlog(level: int) -> None:
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    json_formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
        foreign_pre_chain=shared_processors,
    )
    human_formatter = structlog.stdlib.ProcessorFormatter(
        processor=_human_renderer,
        foreign_pre_chain=shared_processors,
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(json_formatter)
    stdout_handler.setLevel(level)
    root.addHandler(stdout_handler)

    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(human_formatter)
    file_handler.setLevel(level)
    root.addHandler(file_handler)


def _configure_stdlib(level: int) -> None:
    """Fallback when structlog is unavailable."""
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(fmt)
    root.addHandler(stdout_handler)

    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)


def get_logger(name: str) -> Any:
    """Return a logger bound to the given module name."""
    if STRUCTLOG_AVAILABLE:
        return structlog.get_logger(name)
    return logging.getLogger(name)


def bind_request(request_id: str) -> None:
    """Attach request_id to all subsequent log lines in this context."""
    if STRUCTLOG_AVAILABLE and bind_contextvars is not None:
        bind_contextvars(request_id=request_id)


def clear_request_context() -> None:
    """Clear request-scoped context variables after a request completes."""
    if STRUCTLOG_AVAILABLE and clear_contextvars is not None:
        clear_contextvars()
