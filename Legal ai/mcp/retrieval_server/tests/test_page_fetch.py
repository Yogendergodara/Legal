"""Tests for page_fetch integration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mcp.retrieval_server.config import Settings
from mcp.retrieval_server.integrations.page_fetch import fetch_clean_text


@pytest.mark.asyncio
async def test_fetch_clean_text_returns_expected_shape() -> None:
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "<html><body><p>Legal article content here.</p></body></html>"
    mock_response.url = "https://example.com/article"
    mock_response.headers = {"content-type": "text/html; charset=utf-8"}

    settings = Settings()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response
        with patch("trafilatura.extract", return_value="Legal article content here."):
            with patch("trafilatura.extract_metadata") as mock_meta:
                meta = MagicMock()
                meta.title = "Test Article"
                meta.date = "2024-01-01"
                mock_meta.return_value = meta

                result = await fetch_clean_text(
                    "https://example.com/article",
                    request_id="req-pf-1",
                    settings=settings,
                )

    assert result["url"] == "https://example.com/article"
    assert result["title"] == "Test Article"
    assert "Legal article" in result["text"]
    assert result["raw_html_len"] > 0
    assert "published" in result


@pytest.mark.asyncio
async def test_fetch_does_not_log_full_body(caplog: pytest.LogCaptureFixture) -> None:
    import logging

    caplog.set_level(logging.INFO)
    long_text = "x" * 5000

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = f"<html><body>{long_text}</body></html>"
    mock_response.url = "https://example.com/long"
    mock_response.headers = {"content-type": "text/html; charset=utf-8"}

    settings = Settings()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response
        with patch("trafilatura.extract", return_value=long_text):
            with patch("trafilatura.extract_metadata", return_value=None):
                await fetch_clean_text("https://example.com/long", request_id="req-pf-2", settings=settings)

    assert long_text not in caplog.text


@pytest.mark.asyncio
async def test_fetch_pdf_returns_expected_shape() -> None:
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b"%PDF-1.4 fake bytes"
    mock_response.text = ""
    mock_response.url = "https://example.com/guide.pdf"
    mock_response.headers = {"content-type": "application/pdf"}

    settings = Settings()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response
        with patch(
            "mcp.retrieval_server.integrations.page_fetch._extract_pdf_text",
            return_value=("Compliance guide text here.", "Compliance Guide"),
        ):
            result = await fetch_clean_text(
                "https://example.com/guide.pdf",
                request_id="req-pf-pdf",
                settings=settings,
            )

    assert result["extractor"] == "pdf"
    assert "Compliance guide text" in result["text"]
    assert result["title"] == "Compliance Guide"
    assert result["content_type"] == "application/pdf"
