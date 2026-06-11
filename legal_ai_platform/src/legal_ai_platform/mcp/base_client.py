"""Generic MCP client abstraction for HTTP-backed tool servers."""

from __future__ import annotations

import asyncio
import time
from abc import ABC
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx

from legal_ai_platform.observability.events import Failure, ToolCalled
from legal_ai_platform.observability.hooks import HookRegistry


class MCPClientError(Exception):
    """Raised when an MCP tool call fails after retries."""


class BaseMCPClient(ABC):
    """Base class for HTTP-backed MCP tool clients.

    Subclasses declare ``server_name`` and tool-specific methods that call
    ``_post``. Retries, timeouts, and observability hooks are handled here.

    By default a fresh ``httpx.AsyncClient`` is created per request. This keeps
    the client safe to use from the sync->async bridge, where each call runs in
    its own short-lived event loop (a single long-lived AsyncClient binds its
    connection pool to one loop and breaks across loops). Inject ``http_client``
    to reuse a pooled client when calling from a single, stable event loop.
    """

    server_name: str = "mcp"

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 30.0,
        max_retries: int = 3,
        hooks: HookRegistry | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.hooks = hooks or HookRegistry()
        self._injected_client = http_client

    @asynccontextmanager
    async def _acquire_client(self) -> AsyncIterator[httpx.AsyncClient]:
        """Yield an HTTP client, reusing an injected one or creating a fresh one."""
        if self._injected_client is not None:
            yield self._injected_client
            return
        async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_seconds)) as client:
            yield client

    async def close(self) -> None:
        """Close the injected HTTP client if one was provided and is owned elsewhere.

        Per-call clients close themselves; an injected client is owned by the
        caller, so this is a no-op unless a subclass overrides ownership.
        """
        return None

    async def _post(self, tool_path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST to a tool endpoint with retries and observability."""
        url = f"{self.base_url}{tool_path}"
        tool_name = tool_path.rsplit("/", 1)[-1]
        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            started = time.perf_counter()
            try:
                async with self._acquire_client() as client:
                    response = await client.post(url, json=payload)
                    response.raise_for_status()
                    data = response.json()
                latency_ms = (time.perf_counter() - started) * 1000
                self.hooks.emit(
                    ToolCalled(
                        tool_name=tool_name,
                        server=self.server_name,
                        latency_ms=latency_ms,
                        success=True,
                        metadata={"attempt": attempt, "url": url},
                    )
                )
                return data
            except Exception as exc:  # noqa: BLE001
                latency_ms = (time.perf_counter() - started) * 1000
                last_error = exc
                self.hooks.emit(
                    ToolCalled(
                        tool_name=tool_name,
                        server=self.server_name,
                        latency_ms=latency_ms,
                        success=False,
                        metadata={"attempt": attempt, "error": str(exc)},
                    )
                )
                if attempt < self.max_retries:
                    await asyncio.sleep(min(0.5 * attempt, 2.0))

        self.hooks.emit(
            Failure(
                operation=f"{self.server_name}.{tool_name}",
                error=str(last_error),
                recoverable=False,
            )
        )
        raise MCPClientError(
            f"{self.server_name} tool '{tool_name}' failed after {self.max_retries} attempts: {last_error}"
        ) from last_error

    async def health(self) -> dict[str, Any]:
        """Check server health."""
        async with self._acquire_client() as client:
            response = await client.get(f"{self.base_url}/health")
            response.raise_for_status()
            return response.json()
