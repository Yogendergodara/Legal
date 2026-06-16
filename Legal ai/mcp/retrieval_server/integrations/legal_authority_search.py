"""Search authoritative Indian legal websites via public web search (no API key).

Uses DuckDuckGo site-restricted queries to discover judgments and statutes on
official and widely-used primary sources, then returns normalized hits for the
unified search fan-out. Full text is retrieved separately via fetch_url.

Speed strategy
--------------
indiankanoon.org is a comprehensive aggregator that already indexes every Indian
court (SC, all HCs, tribunals, CAT, etc.).  Searching the 16+ individual HC
official sites is almost always redundant and multiplies DDG calls for no gain.

We therefore use a two-tier approach:

  Tier-0 (always): 3 core sources — indiankanoon, indiacode, digiscr.
                   These 3 × 2 query expansions = 6 parallel DDG calls.
                   If they return ≥ MIN_TIER0_RESULTS hits, we stop here.

  Tier-1 (fallback): 5 major HC portals + main SC site, run with the raw query
                     only.  Skipped most of the time; adds only 6 more calls
                     when needed.

Maximum calls per /search request: 18  (down from 80).
Wall-clock ceiling: LEGAL_AUTHORITY_GLOBAL_TIMEOUT (default 18 s).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from urllib.parse import urlparse

from mcp.retrieval_server.integrations.web_search import _duckduckgo_search_sync
from mcp.retrieval_server.logging_setup import get_logger, truncate

logger = get_logger(__name__)

# ── Domain tiers ─────────────────────────────────────────────────────────────

# Tier-0: always searched.  Covers the vast majority of case law.
_TIER0_DOMAINS: tuple[tuple[str, str], ...] = (
    ("indiankanoon.org", "indiankanoon"),   # aggregates ALL Indian courts
    ("indiacode.nic.in", "indiacode"),      # official statute text
    ("digiscr.sci.gov.in", "escr"),         # SC e-SCR neutral citations
)

# Tier-1: fallback when tier-0 yields too few results.
# Only the most-frequently-cited HC portals are included to keep calls bounded.
_TIER1_DOMAINS: tuple[tuple[str, str], ...] = (
    ("main.sci.gov.in", "supremecourt"),
    ("delhihighcourt.nic.in", "delhi_hc"),
    ("bombayhighcourt.nic.in", "bombay_hc"),
    ("allahabadhighcourt.gov.in", "allahabad_hc"),
    ("hcmadras.tn.gov.in", "madras_hc"),
    ("karnatakajudiciary.kar.nic.in", "karnataka_hc"),
)

# ── Query expansion ───────────────────────────────────────────────────────────

# Focused on retrieval quality, not breadth.  Three variants, capped to 2.
_QUERY_EXPANSIONS: tuple[str, ...] = (
    "{query}",
    "{query} judgment",
    "{query} act section",
)

_MAX_TIER0_EXPANSIONS = 2
_MAX_TIER1_EXPANSIONS = 1   # only raw query for tier-1
_MIN_TIER0_RESULTS = 3      # skip tier-1 if tier-0 already found this many


def _expand_queries(query: str, max_count: int) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for template in _QUERY_EXPANSIONS:
        if len(result) >= max_count:
            break
        variant = template.format(query=query).strip()
        if variant not in seen:
            seen.add(variant)
            result.append(variant)
    return result


def _url_matches_domain(url: str, domain: str) -> bool:
    host = urlparse(url).netloc.lower()
    return host == domain or host.endswith(f".{domain}")


def _normalize_hit(
    item: dict[str, Any], domain: str, backend: str, rank: int
) -> dict[str, Any] | None:
    url = str(item.get("url") or item.get("href") or "").strip()
    if not url or not _url_matches_domain(url, domain):
        return None
    title = str(item.get("title") or "Untitled")
    snippet = str(item.get("snippet") or item.get("body") or "")
    return {
        "url": url,
        "title": title,
        "snippet": snippet,
        "score": max(0.55, 0.92 - rank * 0.05),
        "engine": backend,
        "metadata": {
            "backend": backend,
            "domain": domain,
            "search_method": "site_restricted_web",
        },
    }


class LegalAuthoritySearchClient:
    """Find Indian legal primary sources using site-restricted web search.

    Parameters
    ----------
    call_timeout:
        Per-task timeout for each individual DDG site-search call (seconds).
        A short value avoids a single blocked engine from blocking everything.
    global_timeout:
        Hard wall-clock budget for the entire ``search()`` call (seconds).
        We return whatever we have collected when this fires.
    """

    def __init__(
        self,
        call_timeout: float = 8.0,
        global_timeout: float = 18.0,
        # Legacy kwarg kept for call-sites that still pass `timeout=`.
        timeout: float | None = None,
    ) -> None:
        self._call_timeout = timeout if timeout is not None else call_timeout
        self._global_timeout = global_timeout

    async def _search_domain(
        self,
        query: str,
        domain: str,
        backend: str,
        max_results: int,
        request_id: str,
    ) -> tuple[list[dict[str, Any]], bool]:
        site_query = f"site:{domain} {query}"
        logger.info(
            "calling legal authority web search",
            request_id=request_id,
            source=backend,
            domain=domain,
            query=truncate(site_query, 200),
            limit=max_results,
        )
        start = time.perf_counter()
        try:
            raw = await asyncio.wait_for(
                asyncio.to_thread(_duckduckgo_search_sync, site_query, max_results, self._call_timeout),
                timeout=self._call_timeout,
            )
            duration_ms = int((time.perf_counter() - start) * 1000)
            hits = [
                hit
                for idx, item in enumerate(raw)
                if isinstance(item, dict)
                for hit in [_normalize_hit(item, domain, backend, idx)]
                if hit is not None
            ]
            logger.info(
                "legal authority web search responded",
                request_id=request_id,
                source=backend,
                count=len(hits),
                duration_ms=duration_ms,
            )
            return hits, False
        except asyncio.TimeoutError:
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.warning(
                "legal authority web search timeout",
                request_id=request_id,
                source=backend,
                domain=domain,
                duration_ms=duration_ms,
                action="skipped_source",
            )
            return [], True
        except Exception as exc:
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.error(
                "legal authority web search failed",
                request_id=request_id,
                source=backend,
                domain=domain,
                error=type(exc).__name__,
                message=str(exc),
                duration_ms=duration_ms,
                exc_info=True,
            )
            return [], True

    def _merge(
        self,
        merged: dict[str, dict[str, Any]],
        gathered: list[tuple[list[dict[str, Any]], bool]],
    ) -> bool:
        """Merge gathered domain results into `merged`, return True if any degraded."""
        degraded = False
        for hits, domain_degraded in gathered:
            if domain_degraded:
                degraded = True
            for hit in hits:
                url = hit["url"]
                existing = merged.get(url)
                if existing is None or hit["score"] > existing["score"]:
                    merged[url] = hit
        return degraded

    async def _run_search(
        self,
        query: str,
        max_results: int,
        request_id: str,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Inner search logic (no global timeout wrapper here)."""
        per_domain = max(3, (max_results + len(_TIER0_DOMAINS) - 1) // len(_TIER0_DOMAINS))

        # ── Tier-0: always run (3 domains × 2 expansions = 6 tasks) ──────────
        tier0_queries = _expand_queries(query, _MAX_TIER0_EXPANSIONS)
        tier0_tasks = [
            self._search_domain(q, domain, backend, per_domain, request_id)
            for q in tier0_queries
            for domain, backend in _TIER0_DOMAINS
        ]
        tier0_raw: list[tuple[list[dict[str, Any]], bool]] = await asyncio.gather(*tier0_tasks)

        merged: dict[str, dict[str, Any]] = {}
        degraded = self._merge(merged, tier0_raw)

        logger.info(
            "tier-0 legal authority search complete",
            request_id=request_id,
            unique_hits=len(merged),
            degraded=degraded,
        )

        # ── Early exit: skip tier-1 when tier-0 already has enough ───────────
        if len(merged) >= _MIN_TIER0_RESULTS:
            ranked = sorted(merged.values(), key=lambda x: x["score"], reverse=True)
            return ranked[:max_results], degraded

        # ── Tier-1: fallback (6 domains × 1 expansion = 6 tasks) ─────────────
        tier1_queries = _expand_queries(query, _MAX_TIER1_EXPANSIONS)
        tier1_per_domain = max(3, (max_results + len(_TIER1_DOMAINS) - 1) // len(_TIER1_DOMAINS))
        tier1_tasks = [
            self._search_domain(q, domain, backend, tier1_per_domain, request_id)
            for q in tier1_queries
            for domain, backend in _TIER1_DOMAINS
        ]
        tier1_raw: list[tuple[list[dict[str, Any]], bool]] = await asyncio.gather(*tier1_tasks)
        tier1_degraded = self._merge(merged, tier1_raw)
        if tier1_degraded:
            degraded = True

        logger.info(
            "tier-1 legal authority search complete",
            request_id=request_id,
            unique_hits=len(merged),
            degraded=degraded,
        )

        ranked = sorted(merged.values(), key=lambda x: x["score"], reverse=True)
        return ranked[:max_results], degraded

    async def search(
        self,
        query: str,
        max_results: int,
        request_id: str = "-",
    ) -> tuple[list[dict[str, Any]], bool]:
        """Search primary Indian legal domains with a global wall-clock budget.

        Returns (results, degraded).
        """
        if not query.strip():
            return [], False

        try:
            return await asyncio.wait_for(
                self._run_search(query, max_results, request_id),
                timeout=self._global_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "legal authority search hit global timeout",
                request_id=request_id,
                global_timeout=self._global_timeout,
                action="returning_empty",
            )
            return [], True
