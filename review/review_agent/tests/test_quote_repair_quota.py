"""Tests for quote repair fail-open on quota pressure (Phase B-RC)."""

from __future__ import annotations

import pytest

from review_agent.services.quote_repair_llm import QuoteRepairJob, QuoteRepairResult, repair_quotes_batch


@pytest.mark.asyncio
async def test_repair_quotes_batch_skips_on_429(monkeypatch):
    from review_agent.config import get_settings

    async def _raise_429(*_args, **_kwargs):
        raise RuntimeError("429 rate limit exceeded")

    monkeypatch.setattr(
        "review_agent.services.quote_repair_llm.get_review_model",
        lambda **kwargs: object(),
    )
    monkeypatch.setattr(
        "review_agent.services.quote_repair_llm.invoke_structured",
        _raise_429,
    )
    cfg = get_settings().model_copy(
        update={
            "quote_repair_enabled": True,
            "quote_repair_batch_enabled": True,
            "quote_repair_batch_size": 2,
        }
    )
    jobs = [
        QuoteRepairJob(
            repair_id="r1",
            section_id="1",
            source_text="The vendor shall indemnify the buyer.",
            candidate_quote="indemnify",
        ),
        QuoteRepairJob(
            repair_id="r2",
            section_id="2",
            source_text="Payment is due within thirty days.",
            candidate_quote="thirty days",
        ),
    ]
    out = await repair_quotes_batch(jobs, settings=cfg)
    assert out["r1"].repair_notes == "quote repair skipped: rate limited"
    assert out["r2"].repair_notes == "quote repair skipped: rate limited"


@pytest.mark.asyncio
async def test_repair_quotes_batch_skips_on_wrapped_httpx_429(monkeypatch):
    import httpx
    from review_agent.config import get_settings

    async def _raise_wrapped_429(*_args, **_kwargs):
        request = httpx.Request("POST", "https://api.mistral.ai/v1/chat/completions")
        response = httpx.Response(429, request=request)
        raise RuntimeError("invoke failed") from httpx.HTTPStatusError(
            "429 Too Many Requests",
            request=request,
            response=response,
        )

    monkeypatch.setattr(
        "review_agent.services.quote_repair_llm.get_review_model",
        lambda **kwargs: object(),
    )
    monkeypatch.setattr(
        "review_agent.services.quote_repair_llm.invoke_structured",
        _raise_wrapped_429,
    )
    cfg = get_settings().model_copy(
        update={
            "quote_repair_enabled": True,
            "quote_repair_batch_enabled": True,
            "quote_repair_batch_size": 2,
        }
    )
    jobs = [
        QuoteRepairJob(
            repair_id="r1",
            section_id="1",
            source_text="The vendor shall indemnify the buyer.",
            candidate_quote="indemnify",
        ),
        QuoteRepairJob(
            repair_id="r2",
            section_id="2",
            source_text="Payment is due within thirty days.",
            candidate_quote="thirty days",
        ),
    ]
    stats: dict[str, int] = {}
    out = await repair_quotes_batch(jobs, settings=cfg, stats=stats)
    assert out["r1"].repair_notes == "quote repair skipped: rate limited"
    assert stats.get("quote_repair_quota_skipped") == 2


@pytest.mark.asyncio
async def test_repair_quotes_batch_hot_skips_fan_out(monkeypatch):
    from review_agent.config import get_settings
    from review_agent.resilience.failure_policy import ReviewPosture

    calls = {"single": 0}

    async def _raise_structure(*_args, **_kwargs):
        raise ValueError("Expecting value: line 1 column 1 (char 0)")

    async def _single(*_args, **_kwargs):
        calls["single"] += 1
        return QuoteRepairResult(repair_notes="should not run")

    monkeypatch.setattr(
        "review_agent.services.quote_repair_llm.get_review_model",
        lambda **kwargs: object(),
    )
    monkeypatch.setattr(
        "review_agent.services.quote_repair_llm.invoke_structured",
        _raise_structure,
    )
    monkeypatch.setattr(
        "review_agent.services.quote_repair_llm.repair_quote_for_section",
        _single,
    )
    monkeypatch.setattr(
        "review_agent.services.quote_repair_llm.get_current_review_posture",
        lambda: ReviewPosture.HOT,
    )
    cfg = get_settings().model_copy(
        update={
            "quote_repair_enabled": True,
            "quote_repair_batch_enabled": True,
            "quote_repair_batch_size": 2,
        }
    )
    jobs = [
        QuoteRepairJob(
            repair_id="r1",
            section_id="1",
            source_text="Alpha clause text here.",
            candidate_quote="Alpha",
        ),
        QuoteRepairJob(
            repair_id="r2",
            section_id="2",
            source_text="Beta clause text here.",
            candidate_quote="Beta",
        ),
    ]
    out = await repair_quotes_batch(jobs, settings=cfg)
    assert calls["single"] == 0
    assert out["r1"].repair_notes == "quote repair skipped: rate limited"
