"""Authority tier classification and ranking boosts for search results."""

from __future__ import annotations

from urllib.parse import urlparse

from mcp.retrieval_server.models import SearchResult

_PRIMARY_DOMAINS = (
    "indiankanoon.org",
    "indiacode.nic.in",
    "digiscr.sci.gov.in",
    "main.sci.gov.in",
    "sci.gov.in",
)

_BLOG_DOMAINS = (
    "lawsikho.com",
    "ipleaders.in",
    "clearias.com",
    "medium.com",
)


def classify_result_tier(result: SearchResult) -> str:
    """Return authority tier label for a search result."""
    metadata = result.metadata or {}
    backend = str(metadata.get("backend", "")).lower()
    if backend in ("indiankanoon", "escr", "indiacode", "supremecourt"):
        return "primary"

    url = (result.url or "").lower()
    for domain in _PRIMARY_DOMAINS:
        if domain in url:
            return "primary"
    if url.endswith(".gov.in") or url.endswith(".nic.in"):
        return "primary"

    host = urlparse(url).netloc.lower()
    for blog in _BLOG_DOMAINS:
        if blog in host:
            return "unknown"
    if host.endswith(".edu") or host.endswith(".ac.in"):
        return "secondary"
    return "unknown"


def authority_score_boost(result: SearchResult) -> float:
    """Compute relevance score boost/penalty from authority tier."""
    metadata = result.metadata or {}
    backend = str(metadata.get("backend", "")).lower()
    url = (result.url or "").lower()

    boost = 0.0
    if backend == "indiankanoon":
        boost += 0.35
    if "indiacode.nic.in" in url or "digiscr.sci.gov.in" in url:
        boost += 0.30
    elif url.endswith(".gov.in") or url.endswith(".nic.in"):
        boost += 0.25

    host = urlparse(url).netloc.lower()
    for blog in _BLOG_DOMAINS:
        if blog in host:
            boost -= 0.40
            break
    return boost


def apply_authority_metadata(result: SearchResult) -> SearchResult:
    """Attach authority tier metadata and boosted score to a result."""
    tier = classify_result_tier(result)
    metadata = dict(result.metadata or {})
    metadata["authority_tier"] = tier
    boosted = round(
        min(max(result.relevance_score + authority_score_boost(result), 0.0), 1.0),
        2,
    )
    return result.model_copy(update={"metadata": metadata, "relevance_score": boosted})
