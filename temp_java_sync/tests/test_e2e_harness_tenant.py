"""Tests for E2E harness tenant parity (Phase O)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx

from e2e_harness import sync_policies


def test_sync_policies_sends_fixture_tenant_id() -> None:
    captured: dict = {}

    async def fake_post(url: str, json: dict) -> MagicMock:
        captured["url"] = url
        captured["json"] = json
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json = MagicMock(return_value={"tenant_id": "acme-nda-clean", "policies": []})
        return response

    http = MagicMock(spec=httpx.AsyncClient)
    http.post = AsyncMock(side_effect=fake_post)

    result = asyncio.run(
        sync_policies(
            http,
            [{"policy_ref": "p1", "title": "P", "text": "body"}],
            tenant_id="acme-nda-clean",
            replace=True,
        )
    )

    assert captured["json"]["tenant_id"] == "acme-nda-clean"
    assert captured["json"]["replace_tenant_policies"] is True
    assert result["tenant_id"] == "acme-nda-clean"


def test_sync_policies_omits_tenant_when_not_set() -> None:
    captured: dict = {}

    async def fake_post(url: str, json: dict) -> MagicMock:
        captured["json"] = json
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json = MagicMock(return_value={"tenant_id": "e2e-demo", "policies": []})
        return response

    http = MagicMock(spec=httpx.AsyncClient)
    http.post = AsyncMock(side_effect=fake_post)

    asyncio.run(sync_policies(http, [{"policy_ref": "p1", "title": "P", "text": "body"}]))

    assert "tenant_id" not in captured["json"]
