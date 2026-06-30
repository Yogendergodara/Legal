"""Tests for process-global MCP concurrency limiter."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from review_agent.clients.document_client import DocumentMCPClient
from review_agent.errors import RecoverableError
from review_agent.resilience.mcp_limiter import (
    get_mcp_limiter_stats,
    mcp_concurrency_slot,
    reset_mcp_limiter,
    reset_mcp_limiter_stats,
)


@pytest.fixture(autouse=True)
def _reset_limiter(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MCP_GLOBAL_CONCURRENCY", "2")
    monkeypatch.setenv("MCP_SEMAPHORE_ACQUIRE_TIMEOUT_SECONDS", "0.15")
    monkeypatch.setenv("MCP_SEMAPHORE_ACQUIRE_WARN_SECONDS", "0.05")
    from review_agent.config import get_settings

    get_settings.cache_clear()
    reset_mcp_limiter()
    yield
    reset_mcp_limiter()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_semaphore_limits_concurrent_requests():
    in_flight = 0
    max_seen = 0
    lock = asyncio.Lock()

    async def slow_request(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, max_seen
        async with lock:
            in_flight += 1
            max_seen = max(max_seen, in_flight)
        await asyncio.sleep(0.08)
        async with lock:
            in_flight -= 1
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(slow_request)
    http_client = httpx.AsyncClient(transport=transport, base_url="http://mcp.test")
    client = DocumentMCPClient("http://mcp.test", http_client=http_client, max_retries=1)

    await asyncio.gather(
        client._request("GET", "/health"),
        client._request("GET", "/health"),
        client._request("GET", "/health"),
        client._request("GET", "/health"),
    )
    await client.aclose()

    assert max_seen <= 2


@pytest.mark.asyncio
async def test_acquire_timeout_raises_recoverable_error():
    release = asyncio.Event()

    async def hold_both_slots() -> None:
        async with mcp_concurrency_slot(method="GET", path="/hold-1"):
            async with mcp_concurrency_slot(method="GET", path="/hold-2"):
                release.set()
                await asyncio.sleep(2.0)

    holder = asyncio.create_task(hold_both_slots())
    await release.wait()

    with pytest.raises(RecoverableError, match="mcp_semaphore_timeout"):
        async with mcp_concurrency_slot(method="GET", path="/blocked"):
            pass

    stats = get_mcp_limiter_stats()
    assert stats["mcp_semaphore_acquire_timeouts"] == 1
    holder.cancel()
    with pytest.raises(asyncio.CancelledError):
        await holder


@pytest.mark.asyncio
async def test_long_wait_logs_contention_and_records_stats(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("MCP_SEMAPHORE_ACQUIRE_TIMEOUT_SECONDS", "2")
    from review_agent.config import get_settings

    get_settings.cache_clear()
    reset_mcp_limiter_stats()
    caplog.set_level("WARNING")
    release = asyncio.Event()

    async def hold_both_slots() -> None:
        async with mcp_concurrency_slot(method="POST", path="/hold-1"):
            async with mcp_concurrency_slot(method="POST", path="/hold-2"):
                release.set()
                await asyncio.sleep(0.12)

    holder = asyncio.create_task(hold_both_slots())
    await release.wait()

    async def waiter() -> None:
        async with mcp_concurrency_slot(method="POST", path="/tools/search_policy"):
            pass

    await waiter()
    await holder

    stats = get_mcp_limiter_stats()
    assert stats["mcp_semaphore_contention_events"] >= 1
    assert "mcp_semaphore_contention" in caplog.text


@pytest.mark.asyncio
async def test_limiter_is_process_global_across_client_instances():
    release = asyncio.Event()

    async def hold_both_slots() -> None:
        async with mcp_concurrency_slot(method="GET", path="/hold-1"):
            async with mcp_concurrency_slot(method="GET", path="/hold-2"):
                release.set()
                await asyncio.sleep(2.0)

    holder = asyncio.create_task(hold_both_slots())
    await release.wait()

    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"status": "ok"})
    )
    http_client = httpx.AsyncClient(transport=transport, base_url="http://mcp.test")
    client = DocumentMCPClient("http://mcp.test", http_client=http_client, max_retries=1)
    try:
        with pytest.raises(RecoverableError, match="mcp_semaphore_timeout"):
            await client._request("GET", "/health")
    finally:
        await client.aclose()
        holder.cancel()
        with pytest.raises(asyncio.CancelledError):
            await holder
