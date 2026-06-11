"""Content extraction and hashing for crawled pages."""

from __future__ import annotations

import hashlib
from typing import Any

import trafilatura


def compute_content_hash(text: str) -> str:
    """SHA-256 hash of cleaned text for deduplication."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def extract_page(html: str, url: str) -> dict[str, Any]:
    """Extract clean text and metadata from HTML using trafilatura."""
    clean_text = trafilatura.extract(html, include_comments=False) or ""
    meta = trafilatura.extract_metadata(html)
    content_hash = compute_content_hash(clean_text) if clean_text else ""

    return {
        "url": url,
        "title": getattr(meta, "title", None) if meta else None,
        "clean_text": clean_text,
        "content_hash": content_hash,
        "published_at": getattr(meta, "date", None) if meta else None,
        "canonical_url": getattr(meta, "url", None) if meta else url,
    }
