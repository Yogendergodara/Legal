"""Tests for review preflight gates."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from review_agent.clients.document_client import DocumentMCPClient
from review_agent.services.review_preflight import (
    ReviewPreflightError,
    check_llm_credentials,
    check_mcp_search_metadata_capability,
    run_review_preflight,
)
from document_core.schemas.registry import ListPolicyRegistryResponse, PolicyRegistryRecord
from uuid import uuid4


def test_check_llm_credentials_missing(monkeypatch):
    from review_agent.config import get_settings

    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEYS", raising=False)
    monkeypatch.setenv("LLM_KEY_POOL_ENABLED", "false")
    get_settings.cache_clear()
    with pytest.raises(ReviewPreflightError, match="LLM credentials"):
        check_llm_credentials()


def test_check_llm_credentials_with_key_pool(monkeypatch):
    from review_agent.config import get_settings

    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("LLM_KEY_POOL_ENABLED", "true")
    monkeypatch.setenv("LLM_API_KEYS", "key-a,key-b")
    get_settings.cache_clear()
    check_llm_credentials()


def test_check_llm_credentials_with_api_key(monkeypatch):
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    check_llm_credentials()


def test_check_llm_credentials_with_base_url(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:8000/v1")
    check_llm_credentials()


@pytest.mark.asyncio
async def test_preflight_disabled_skips_checks(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    client = AsyncMock(spec=DocumentMCPClient)
    await run_review_preflight(client, preflight_enabled=False)
    client.health.assert_not_called()


@pytest.mark.asyncio
async def test_preflight_fails_on_unhealthy_mcp(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    client = AsyncMock(spec=DocumentMCPClient)
    client.health = AsyncMock(return_value={"status": "degraded", "db": "error"})
    with pytest.raises(ReviewPreflightError, match="unhealthy"):
        await run_review_preflight(client)


@pytest.mark.asyncio
async def test_preflight_passes_when_healthy(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    client = AsyncMock(spec=DocumentMCPClient)
    client.base_url = "http://localhost:8003"
    client.timeout_seconds = 5.0
    client._injected_client = None
    client.health = AsyncMock(
        return_value={
            "status": "ok",
            "db": "ok",
            "capabilities": ["search_request_metadata"],
        }
    )
    monkeypatch.setattr(
        "review_agent.services.review_preflight.check_mcp_search_metadata_capability",
        AsyncMock(),
    )
    await run_review_preflight(client)


@pytest.mark.asyncio
async def test_preflight_scoped_policy_not_indexed(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    policy_id = uuid4()
    client = AsyncMock(spec=DocumentMCPClient)
    client.health = AsyncMock(return_value={"status": "ok", "db": "ok"})
    client.list_policy_registry = AsyncMock(
        return_value=ListPolicyRegistryResponse(
            tenant_id="t",
            policies=[
                PolicyRegistryRecord(
                    tenant_id="t",
                    document_id=policy_id,
                    policy_ref="p1",
                    title="Policy",
                    index_status="pending",
                )
            ],
        )
    )
    monkeypatch.setattr(
        "review_agent.services.review_preflight.check_mcp_search_metadata_capability",
        AsyncMock(),
    )
    with pytest.raises(ReviewPreflightError, match="index_status=pending"):
        await run_review_preflight(
            client,
            tenant_id="t",
            policy_document_ids=[str(policy_id)],
        )


@pytest.mark.asyncio
async def test_preflight_warns_general_only_policy(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    policy_id = uuid4()
    client = AsyncMock(spec=DocumentMCPClient)
    client.health = AsyncMock(return_value={"status": "ok", "db": "ok"})
    client.list_policy_registry = AsyncMock(
        return_value=ListPolicyRegistryResponse(
            tenant_id="t",
            policies=[
                PolicyRegistryRecord(
                    tenant_id="t",
                    document_id=policy_id,
                    policy_ref="p1",
                    title="Policy",
                    index_status="indexed",
                    metadata={"categories": ["general"]},
                )
            ],
        )
    )
    monkeypatch.setattr(
        "review_agent.services.review_preflight.check_mcp_search_metadata_capability",
        AsyncMock(),
    )
    warnings = await run_review_preflight(
        client,
        tenant_id="t",
        policy_document_ids=[str(policy_id)],
    )
    assert any("general categories" in w for w in warnings)


@pytest.mark.asyncio
async def test_preflight_includes_config_advisory(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    client = AsyncMock(spec=DocumentMCPClient)
    client.health = AsyncMock(return_value={"status": "ok", "db": "ok"})
    monkeypatch.setattr(
        "review_agent.services.review_preflight.check_mcp_search_metadata_capability",
        AsyncMock(),
    )
    from review_agent.config import ReviewSettings

    warnings = await run_review_preflight(
        client,
        tenant_id="demo",
        settings=ReviewSettings(section_classify_mode="llm_only"),
    )
    assert any(w.startswith("config_advisory:warn:E1:") for w in warnings)


@pytest.mark.asyncio
async def test_preflight_rejects_stale_mcp_metadata_error(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    class _HealthResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"status": "ok", "db": "ok", "capabilities": []}

    class _FakeHttp:
        async def request(self, method: str, url: str, **kwargs):
            return _HealthResponse()

    client = DocumentMCPClient("http://localhost:8003", http_client=_FakeHttp())  # type: ignore[arg-type]
    with pytest.raises(ReviewPreflightError, match="stale process"):
        await check_mcp_search_metadata_capability(client)


@pytest.mark.asyncio
async def test_preflight_probe_http_500_metadata(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    class _HealthResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "status": "ok",
                "db": "ok",
                "capabilities": ["search_request_metadata"],
            }

    class _ProbeResponse:
        status_code = 500
        text = "'SearchRequest' object has no attribute 'metadata'"

        def raise_for_status(self) -> None:
            return None

    class _FakeHttp:
        async def request(self, method: str, url: str, **kwargs):
            if url.endswith("/health"):
                return _HealthResponse()
            return _ProbeResponse()

    client = DocumentMCPClient("http://localhost:8003", http_client=_FakeHttp())  # type: ignore[arg-type]
    with pytest.raises(ReviewPreflightError, match="stale process"):
        await check_mcp_search_metadata_capability(client)
