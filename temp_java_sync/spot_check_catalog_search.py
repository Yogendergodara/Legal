#!/usr/bin/env python3
"""IPC-2.2 — Spot-check catalog search for routing-golden obligation queries."""

from __future__ import annotations

import asyncio
import os
import sys

from bootstrap_env import load_env, setup_pythonpath

load_env()
setup_pythonpath()

from document_core.schemas.policy_catalog import CatalogSearchRequest  # noqa: E402
from review_agent.clients.document_client import DocumentMCPClient  # noqa: E402

MIN_SCORE = 0.25
TENANT = "e2e-demo"
QUERIES: tuple[tuple[str, str], ...] = (
    ("security incident customer notification", "Incident Response"),
    ("breach notification", "Incident Response"),
    ("security practices encryption", "Security Practices"),
    ("data retention schedules deletion", "Data Retention"),
)


async def main() -> int:
    base_url = os.environ.get("DOCUMENT_SERVER_URL", "http://localhost:8003")
    client = DocumentMCPClient(base_url.rstrip("/"))
    failures: list[str] = []

    for query, expect_substr in QUERIES:
        hits = await client.search_policy_catalog(
            CatalogSearchRequest(tenant_id=TENANT, query=query, top_k=3)
        )
        top = [(h.title, round(float(h.score), 3)) for h in hits[:3]]
        print(f"{query!r} -> {top}")
        if not hits:
            failures.append(f"{query!r}: no hits")
            continue
        if hits[0].score < MIN_SCORE:
            failures.append(f"{query!r}: top score {hits[0].score:.3f} < {MIN_SCORE}")
        if expect_substr.lower() not in (hits[0].title or "").lower():
            failures.append(
                f"{query!r}: expected title containing {expect_substr!r}, got {hits[0].title!r}"
            )

    if failures:
        for item in failures:
            print(f"FAIL: {item}", file=sys.stderr)
        return 1

    print("OK: catalog spot-check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
