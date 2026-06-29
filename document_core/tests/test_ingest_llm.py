"""Phase F — ingest LLM 429 retry."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import BaseModel

from document_core.llm import ingest_llm


class _SampleSchema(BaseModel):
    ok: bool


@pytest.mark.asyncio
async def test_invoke_structured_json_retries_429(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_RATE_LIMIT_MAX_RETRIES", "2")
    monkeypatch.setenv("LLM_RATE_LIMIT_BACKOFF_BASE_SECONDS", "0.01")
    monkeypatch.setenv("LLM_RATE_LIMIT_BACKOFF_MAX_SECONDS", "0.05")

    ok_response = MagicMock()
    ok_response.status_code = 200
    ok_response.json.return_value = {
        "choices": [{"message": {"content": json.dumps({"ok": True})}}],
    }
    ok_response.raise_for_status = MagicMock()

    rate_response = MagicMock()
    rate_response.status_code = 429
    rate_response.request = MagicMock()

    post = AsyncMock(side_effect=[rate_response, ok_response])
    client = MagicMock()
    client.post = post
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)

    with patch("document_core.llm.ingest_llm.httpx.AsyncClient", return_value=client):
        with patch("document_core.llm.ingest_llm.asyncio.sleep", new=AsyncMock()):
            result = await ingest_llm.invoke_structured_json(
                model="mistral-small",
                system="sys",
                user="usr",
                schema=_SampleSchema,
            )

    assert result.ok is True
    assert post.await_count == 2


def test_ingest_llm_prefers_sync_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYNC_LLM_API_KEY", "sync-key")
    monkeypatch.setenv("LLM_API_KEY", "shared-key")
    assert ingest_llm._api_key() == "sync-key"


def test_ingest_llm_api_key_available_with_sync_key_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.setenv("SYNC_LLM_API_KEY", "sync-only")
    assert ingest_llm.llm_api_key_available() is True
