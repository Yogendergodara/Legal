"""Async concurrency helpers for hybrid compliance."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar

T = TypeVar("T")


async def gather_limited(
    coros: Sequence[Awaitable[T]],
    *,
    limit: int,
) -> list[T | BaseException]:
    """Run awaitables with a concurrency cap; preserves result order."""
    if not coros:
        return []
    semaphore = asyncio.Semaphore(max(1, limit))

    async def run(coro: Awaitable[T]) -> T:
        async with semaphore:
            return await coro

    return list(await asyncio.gather(*(run(c) for c in coros), return_exceptions=True))


async def map_limited(
    items: Sequence[T],
    func: Callable[[T], Awaitable[T]],
    *,
    limit: int,
) -> list[T | BaseException]:
    return await gather_limited([func(item) for item in items], limit=limit)
