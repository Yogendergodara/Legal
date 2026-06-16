"""Deterministic legal research bootstrap (no LLM).

Runs keyword search + primary-source fetches before the multi-agent supervisor
so the pipeline always has real URLs and excerpts to cite — even when the LLM
research loop is rate-limited or weak on tool calling.
"""

from __future__ import annotations

import re

from deep_research_from_scratch.config import config
from deep_research_from_scratch.retrieval_bridge import run_fetch, run_search
from deep_research_from_scratch.source_registry import (
    RetrievedSource,
    count_fetches,
    merge_retrieved_sources,
    normalize_url,
)

_PRIMARY_HOSTS = (
    "indiankanoon.org",
    "indiacode.nic.in",
    "digiscr.sci.gov.in",
    "sci.gov.in",
    ".gov.in",
)


def _topic_phrase(research_brief: str, user_query: str) -> str:
    text = (user_query or research_brief or "").strip()
    return re.sub(r"\s+", " ", text)[:280]


_CRIMINAL_KEYWORDS = (
    "murder", "theft", "fraud", "cheating", "assault", "rape", "robbery",
    "crime", "offence", "offense", "criminal", "penal", "punishment", "sentence",
    "bail", "arrest", "custody", "accused", "fir", "chargesheet",
    "bns", "ipc", "crpc", "bnss", "bsa", "bharatiya nyaya",
    "cognizable", "non-cognizable", "section 302", "section 103",
    "section 420", "section 316", "section 406", "section 409",
)

_CIVIL_KEYWORDS = (
    "contract", "agreement", "specific performance", "injunction",
    "tort", "negligence", "damages", "property", "transfer", "mortgage",
    "trust", "succession", "inheritance", "will", "probate",
    "consumer", "deficiency", "service", "arbitration",
)

_CONSTITUTIONAL_KEYWORDS = (
    "article 21", "article 14", "article 19", "fundamental right",
    "writ", "habeas corpus", "mandamus", "certiorari", "prohibition",
    "constitutional", "parliament", "legislature",
)

_CRYPTO_KEYWORDS = (
    "crypto", "cryptocurrency", "virtual currency", "bitcoin", "blockchain",
    "digital asset", "vda", "virtual digital asset", "token", "nft", "web3",
)


def _expand_search_queries(topic: str) -> list[str]:
    """Build diverse Indian Kanoon / India Code queries for better case coverage."""
    if not topic:
        return ["site:indiankanoon.org supreme court India"]

    lower = topic.lower()
    # Topic-specific queries run FIRST so they are not cut off by BOOTSTRAP_SEARCH_QUERIES.
    priority: list[str] = []

    if any(word in lower for word in _CRYPTO_KEYWORDS):
        priority.extend([
            'site:indiankanoon.org "Internet and Mobile Association" RBI cryptocurrency',
            "site:indiankanoon.org IAMAI Reserve Bank India virtual currency supreme court",
            "site:indiankanoon.org Internet Mobile Association India RBI 2020 cryptocurrency",
            "site:indiacode.nic.in Prevention of Money Laundering Act virtual digital asset",
            "site:indiankanoon.org PMLA virtual digital asset cryptocurrency ED",
            "site:indiankanoon.org PMLA cryptocurrency enforcement directorate attachment",
            "site:indiankanoon.org RBI cryptocurrency circular banking ban India",
            "site:indiankanoon.org cryptocurrency regulation India supreme court 2023 2024",
        ])

    if any(
        word in lower
        for word in ("freeze", "frozen", "attachment", "seizure", "block", "bank account")
    ):
        priority.extend([
            "site:indiankanoon.org bank account freeze indefinite supreme court",
            "site:indiankanoon.org PMLA bank account freeze 180 days",
            "site:indiankanoon.org Vijay Madanlal Choudhary PMLA attachment",
            "site:indiankanoon.org provisional attachment bank account limitation period",
            "site:indiankanoon.org indefinite freeze bank account quashing",
        ])

    queries: list[str] = priority + [
        f"site:indiankanoon.org {topic}",
        f"site:indiankanoon.org {topic} supreme court",
        f"{topic} judgment India site:indiankanoon.org",
        f"site:indiacode.nic.in {topic}",
    ]

    if any(
        word in lower
        for word in ("freeze", "frozen", "attachment", "seizure", "block", "bank account")
    ):
        queries.extend([
            "site:indiankanoon.org CrPC Section 102 bank account attachment",
            "site:indiankanoon.org P Chidambaram bank account freeze ED",
            "site:indiankanoon.org provisional attachment order bank account PMLA",
            "site:indiankanoon.org bank account freeze high court 2023",
            "site:indiankanoon.org bank account freeze high court 2024",
            "site:indiankanoon.org BNSS Section 106 bank account attachment 2024",
        ])

    if "limitation" in lower or "time limit" in lower or "period" in lower:
        queries.append(f"site:indiankanoon.org {topic} limitation period")

    # Criminal law — always fetch BOTH old (IPC/CrPC) and new (BNS/BNSS) statute text
    if any(word in lower for word in _CRIMINAL_KEYWORDS):
        queries.extend([
            f"site:indiacode.nic.in Bharatiya Nyaya Sanhita {topic}",
            f"site:indiacode.nic.in Bharatiya Nagarik Suraksha Sanhita {topic}",
            f"site:indiacode.nic.in Indian Penal Code {topic}",
            f"site:indiacode.nic.in Code of Criminal Procedure {topic}",
            f"site:indiankanoon.org {topic} BNS BNSS 2024 supreme court",
            f"site:indiankanoon.org {topic} IPC CrPC supreme court",
        ])

    # Civil law — always fetch Transfer of Property / Contract Act where relevant
    if any(word in lower for word in _CIVIL_KEYWORDS):
        queries.extend([
            f"site:indiacode.nic.in {topic}",
            f"site:indiankanoon.org {topic} civil suit high court",
        ])

    # Constitutional matters
    if any(word in lower for word in _CONSTITUTIONAL_KEYWORDS):
        queries.extend([
            f"site:indiankanoon.org {topic} article 21 fundamental rights supreme court",
            f"site:indiankanoon.org writ petition {topic} high court",
        ])

    # Non-site-restricted fallback: ensures results even when DDG blocks site: queries
    queries.extend([
        f"{topic} site:indiankanoon.org judgment",
        f"{topic} supreme court India judgment 2023 OR 2024",
    ])

    # Recent judgment sweep — always include year-specific queries for trends
    queries.extend([
        f"site:indiankanoon.org {topic} 2023 2024",
        f"site:indiankanoon.org {topic} 2022 high court",
    ])

    seen: set[str] = set()
    ordered: list[str] = []
    for query in queries:
        if query not in seen:
            seen.add(query)
            ordered.append(query)
    return ordered


def _is_primary_url(url: str) -> bool:
    url_lower = (url or "").lower()
    return any(host in url_lower for host in _PRIMARY_HOSTS)


def _collect_search_hits(
    queries: list[str],
    results_per_query: int,
) -> tuple[list[str], list[RetrievedSource]]:
    """Run searches and dedupe sources by URL."""
    search_blocks: list[str] = []
    search_sources: list[RetrievedSource] = []
    seen_urls: set[str] = set()

    for query in queries:
        try:
            text, sources = run_search(query, results_per_query)
            search_blocks.append(f"--- Query: {query} ---\n{text}")
            for src in sources:
                key = normalize_url(src.url)
                if key and key not in seen_urls:
                    seen_urls.add(key)
                    search_sources.append(src)
        except Exception as exc:  # noqa: BLE001
            search_blocks.append(f"Search failed ({query}): {exc}")

    return search_blocks, search_sources


def _fetch_primary_sources(
    search_sources: list[RetrievedSource],
    max_fetches: int,
) -> tuple[list[str], list[RetrievedSource]]:
    """Fetch unique primary URLs, preferring Indian Kanoon judgments."""
    primary_urls = [s.url for s in search_sources if _is_primary_url(s.url)]
    other_urls = [s.url for s in search_sources if s.url and s.url not in primary_urls]
    fetch_order = primary_urls + other_urls

    merged_sources = list(search_sources)
    fetch_blocks: list[str] = []
    fetched_urls: set[str] = set()
    fetched_count = 0

    for url in fetch_order:
        if fetched_count >= max_fetches:
            break
        key = normalize_url(url)
        if not key or key in fetched_urls:
            continue
        fetched_urls.add(key)
        try:
            fetch_text, src = run_fetch(url)
            fetch_blocks.append(fetch_text)
            if src:
                merged_sources = merge_retrieved_sources(merged_sources, [src])
                fetched_count += 1
        except Exception as exc:  # noqa: BLE001
            fetch_blocks.append(f"Fetch failed for {url}: {exc}")

    return fetch_blocks, merged_sources


def bootstrap_legal_research(
    research_brief: str,
    user_query: str = "",
) -> tuple[str, str, list[RetrievedSource]]:
    """Search and fetch primary Indian legal sources for a topic.

    Returns:
        (compressed_note, raw_note, merged_sources)
    """
    if not config.ENABLE_RESEARCH_BOOTSTRAP:
        return "", "", []

    topic = _topic_phrase(research_brief, user_query)
    max_queries = config.BOOTSTRAP_SEARCH_QUERIES
    max_fetches = config.BOOTSTRAP_MAX_FETCHES
    results_per_query = config.BOOTSTRAP_RESULTS_PER_QUERY
    min_target = config.BOOTSTRAP_MIN_TARGET_FETCHES

    all_queries = _expand_search_queries(topic)
    search_blocks, merged_sources = _collect_search_hits(
        all_queries[:max_queries],
        results_per_query,
    )

    fetch_blocks, merged_sources = _fetch_primary_sources(merged_sources, max_fetches)

    _, primary_fetches = count_fetches(merged_sources)
    if primary_fetches < min_target and len(all_queries) > max_queries:
        extra_blocks, extra_sources = _collect_search_hits(
            all_queries[max_queries : max_queries + 3],
            results_per_query,
        )
        search_blocks.extend(extra_blocks)
        merged_sources = merge_retrieved_sources(merged_sources, extra_sources)
        extra_fetch_blocks, merged_sources = _fetch_primary_sources(
            extra_sources,
            max(0, max_fetches - primary_fetches),
        )
        fetch_blocks.extend(extra_fetch_blocks)

    if not search_blocks and not fetch_blocks:
        return "", "", []

    raw_note = "\n\n".join(
        [f"BOOTSTRAP RESEARCH for: {topic}", *search_blocks, *fetch_blocks]
    )

    compressed_lines = [
        f"Bootstrap research (deterministic search + fetch) for: {topic}",
        f"Sources retrieved: {len(merged_sources)} "
        f"({sum(1 for s in merged_sources if s.fetched)} fetched).",
        "",
    ]
    for index, src in enumerate(merged_sources, 1):
        status = "FETCHED" if src.fetched else "search snippet"
        line = f"[{index}] {src.title} | {src.url}"
        if src.citation:
            line += f" | {src.citation}"
        compressed_lines.append(f"  ({status}) {line}")
        if src.excerpt:
            compressed_lines.append(f"  Excerpt: {src.excerpt[:1000]}")
    if not merged_sources:
        compressed_lines.append(
            "No sources returned during bootstrap — agent must search and fetch."
        )

    return "\n".join(compressed_lines), raw_note, merged_sources
