"""JSON salvage tests for llm_gateway (Phase 22 P8)."""

from __future__ import annotations

import pytest

from review_agent.models.llm_gateway import _extract_json_payload


def test_json_salvage_extra_data():
    raw = '{"categories": ["liability"]}{"categories": ["indemnity"]}'
    payload = _extract_json_payload(raw)
    assert "items" in payload
    assert len(payload["items"]) == 2


def test_json_array_payload():
    raw = '[{"section_id": "1", "categories": ["liability"]}]'
    payload = _extract_json_payload(raw)
    assert isinstance(payload, list)
    assert payload[0]["categories"] == ["liability"]


def test_json_single_object():
    raw = '{"items": [{"section_id": "2", "categories": ["confidentiality"]}]}'
    payload = _extract_json_payload(raw)
    assert payload["items"][0]["section_id"] == "2"


@pytest.mark.asyncio
async def test_invoke_once_retries_empty_body(monkeypatch):
    from review_agent.models import llm_gateway
    from pydantic import BaseModel

    class DemoSchema(BaseModel):
        value: str

    calls = {"count": 0}

    class FakeModel:
        def with_structured_output(self, schema):
            raise RuntimeError("structured output failed")

        async def ainvoke(self, messages):
            calls["count"] += 1
            content = "" if calls["count"] == 1 else '{"value": "ok"}'
            return type("Resp", (), {"content": content})()

    result = await llm_gateway._invoke_once(
        FakeModel(),
        DemoSchema,
        system="sys",
        user="user",
    )
    assert result.value == "ok"
    assert calls["count"] == 2
