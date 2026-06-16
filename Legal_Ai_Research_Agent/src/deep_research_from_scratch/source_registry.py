"""Structured tracking of retrieved legal sources for citation grounding."""

from __future__ import annotations

import re
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field

AuthorityTier = Literal["primary", "secondary", "unknown"]

_PRIMARY_DOMAINS = (
    # Supreme Court & central repositories
    "indiankanoon.org",
    "indiacode.nic.in",
    "digiscr.sci.gov.in",
    "main.sci.gov.in",
    "sci.gov.in",
    # Major state High Court official sites — all count as primary
    "delhihighcourt.nic.in",
    "bombayhighcourt.nic.in",
    "calcuttahighcourt.gov.in",
    "allahabadhighcourt.gov.in",
    "hcmadras.tn.gov.in",
    "karnatakajudiciary.kar.nic.in",
    "highcourtofap.gov.in",
    "uttarakhandhighcourt.gov.in",
    "patnahighcourt.gov.in",
    "hcraj.nic.in",
    "ghconline.gov.in",
    "mphc.gov.in",
    "punjabandharyana.nic.in",
    "orissahighcourt.nic.in",
    "kerhc.gov.in",
)

_SECONDARY_SUFFIXES = (".edu", ".ac.in")

_BLOG_DOMAINS = (
    "lawsikho.com",
    "ipleaders.in",
    "clearias.com",
    "blog.",
    "medium.com",
)

# Paywalled legal databases — never cite from search snippets alone.
_PAYWALL_DOMAINS = (
    "manupatra.com",
    "manupatrafast.com",
    "scconline.com",
    "jstor.org",
    "heinonline.org",
    "westlaw.com",
    "lexisnexis.com",
    "vlex.com",
)

_BLOCKED_FETCH_MARKERS = (
    "access denied",
    "sign in to continue",
    "sign in or register",
    "subscribe to read",
    "subscription required",
    "login to access",
    "403 forbidden",
    "paywall",
)


class RetrievedSource(BaseModel):
    """One legal source discovered or fetched during research."""

    url: str
    title: str = "Untitled"
    authority_tier: AuthorityTier = "unknown"
    fetched: bool = False
    citation: str | None = None
    excerpt: str = ""
    source_type: str = "web"
    access_denied: bool = False


def is_paywall_url(url: str) -> bool:
    """True for subscription legal databases that block automated fetch."""
    host = urlparse(url or "").netloc.lower()
    if not host:
        return False
    return any(domain in host for domain in _PAYWALL_DOMAINS)


def is_blocked_fetch_content(text: str) -> bool:
    """True when fetched body looks like a login/paywall/error page."""
    body = (text or "").strip()
    if not body or "Placeholder" in body:
        return True
    if len(body) < 40:
        return True
    lower = body.lower()
    return any(marker in lower for marker in _BLOCKED_FETCH_MARKERS)


def filter_citable_sources(sources: list[RetrievedSource] | None) -> list[RetrievedSource]:
    """Sources the memo writer may cite — excludes unfetched paywall URLs."""
    citable: list[RetrievedSource] = []
    for item in sources or []:
        src = item if isinstance(item, RetrievedSource) else RetrievedSource(**item)
        if src.access_denied:
            continue
        if src.fetched:
            citable.append(src)
        elif not is_paywall_url(src.url):
            citable.append(src)
    return citable


def normalize_url(url: str) -> str:
    """Normalize URL for deduplication."""
    parsed = urlparse((url or "").strip().rstrip("/"))
    if not parsed.netloc:
        return (url or "").strip().lower()
    path = parsed.path.rstrip("/") or ""
    return f"{parsed.scheme}://{parsed.netloc.lower()}{path}"


def classify_authority_tier(url: str, metadata: dict[str, Any] | None = None) -> AuthorityTier:
    """Classify a URL by legal authority tier."""
    metadata = metadata or {}
    backend = str(metadata.get("backend", "")).lower()
    if backend == "indiankanoon":
        return "primary"

    host = urlparse(url or "").netloc.lower()
    if not host:
        return "unknown"

    for domain in _PRIMARY_DOMAINS:
        if domain in host or host.endswith(domain):
            return "primary"

    if host.endswith(".gov.in") or host.endswith(".nic.in"):
        return "primary"

    for blog in _BLOG_DOMAINS:
        if blog in host:
            return "unknown"

    for suffix in _SECONDARY_SUFFIXES:
        if host.endswith(suffix):
            return "secondary"

    return "unknown"


def infer_source_type(url: str, metadata: dict[str, Any] | None = None) -> str:
    """Infer source type label from URL and metadata."""
    metadata = metadata or {}
    backend = str(metadata.get("backend", "")).lower()
    if backend == "indiankanoon":
        return "indiankanoon"
    if backend == "semantic":
        return "semantic"

    host = urlparse(url or "").netloc.lower()
    if "indiacode.nic.in" in host:
        return "indiacode"
    if "digiscr.sci.gov.in" in host or "sci.gov.in" in host:
        return "escr"
    return "web"


def _merge_one(existing: RetrievedSource, incoming: RetrievedSource) -> RetrievedSource:
    """Merge two entries for the same URL, preferring richer fetched data."""
    data = existing.model_dump()
    inc = incoming.model_dump()
    for key, value in inc.items():
        if key == "fetched":
            data[key] = existing.fetched or incoming.fetched
        elif key == "access_denied":
            data[key] = existing.access_denied or incoming.access_denied
        elif key == "excerpt":
            if len(incoming.excerpt) > len(existing.excerpt):
                data[key] = incoming.excerpt
        elif key == "citation":
            data[key] = incoming.citation or existing.citation
        elif key == "title":
            if incoming.title and incoming.title != "Untitled":
                data[key] = incoming.title
        elif value not in (None, "", "Untitled", "unknown", "web", False):
            data[key] = value
    return RetrievedSource(**data)


def merge_retrieved_sources(
    left: list[RetrievedSource] | None,
    right: list[RetrievedSource] | None,
) -> list[RetrievedSource]:
    """Reducer: merge source lists by normalized URL."""
    merged: dict[str, RetrievedSource] = {}
    for item in (left or []) + (right or []):
        src = item if isinstance(item, RetrievedSource) else RetrievedSource(**item)
        key = normalize_url(src.url)
        if not key:
            continue
        existing = merged.get(key)
        merged[key] = _merge_one(existing, src) if existing else src
    return list(merged.values())


def sources_from_search_hits(hits: list[dict[str, Any]]) -> list[RetrievedSource]:
    """Build registry entries from MCP search result dicts."""
    sources: list[RetrievedSource] = []
    for hit in hits:
        url = str(hit.get("url") or hit.get("source_id") or "").strip()
        if not url or not url.startswith("http"):
            continue
        metadata = hit.get("metadata") or {}
        citation = metadata.get("citation")
        sources.append(
            RetrievedSource(
                url=url,
                title=str(hit.get("title") or "Untitled"),
                authority_tier=classify_authority_tier(url, metadata),
                fetched=False,
                citation=str(citation) if citation else None,
                excerpt=str(hit.get("text_snippet") or "")[:2000],
                source_type=infer_source_type(url, metadata),
            )
        )
    return sources


def source_from_fetch(url: str, data: dict[str, Any], excerpt_limit: int) -> RetrievedSource | None:
    """Build or update a registry entry from a fetch response."""
    full_text = str(data.get("full_text") or "")
    if is_blocked_fetch_content(full_text):
        return RetrievedSource(
            url=url,
            title=str(data.get("title") or url),
            authority_tier=classify_authority_tier(url, data.get("metadata")),
            fetched=False,
            excerpt="",
            source_type=infer_source_type(url, data.get("metadata")),
            access_denied=True,
        )
    title = str(data.get("title") or url)
    metadata = data.get("metadata") or {}
    return RetrievedSource(
        url=url,
        title=title,
        authority_tier=classify_authority_tier(url, metadata),
        fetched=True,
        citation=metadata.get("citation"),
        excerpt=full_text[:excerpt_limit],
        source_type=infer_source_type(url, metadata),
    )


def count_fetches(sources: list[RetrievedSource]) -> tuple[int, int]:
    """Return (total_fetches, primary_fetches) from a source list."""
    fetched = [s for s in sources if s.fetched]
    primary = [s for s in fetched if s.authority_tier == "primary"]
    return len(fetched), len(primary)


def has_primary_search_urls(sources: list[RetrievedSource]) -> bool:
    """True if any registered (not yet fetched) source is primary tier."""
    return any(s.authority_tier == "primary" for s in sources)


def format_writer_source_registry(sources: list[RetrievedSource]) -> str:
    """Format fetched sources as the ONLY authorities the memo writer may cite."""
    sources = filter_citable_sources(sources)
    if not sources:
        return (
            "(No sources were retrieved. Do NOT cite any case, statute, or section. "
            "For every legal point write: 'NOT FOUND in retrieved sources — "
            "independent verification required.')"
        )

    fetched = [s for s in sources if s.fetched]
    unfetched = [s for s in sources if not s.fetched]

    lines = [
        "Use ONLY the numbered sources below. Each inline [n] MUST map to one entry.",
        f"Total: {len(sources)} registered ({len(fetched)} fetched, {len(unfetched)} snippet-only).",
        "",
    ]
    for index, src in enumerate(sources, 1):
        if src.fetched:
            status = "FETCHED — cite freely with inline [n]"
        else:
            status = "SNIPPET ONLY — MUST still cite as [n] but append '(snippet only — unverified)' after the [n]"
        lines.append(f"[{index}] {src.title}")
        lines.append(f"  URL: {src.url}")
        lines.append(f"  Status: {status} | Tier: {src.authority_tier}")
        if src.citation:
            lines.append(f"  Citation: {src.citation}")
        if src.excerpt:
            lines.append(f"  Excerpt: {src.excerpt[:600]}")
        lines.append("")
    return "\n".join(lines).rstrip()


def build_verification_corpus(
    notes: list[str],
    raw_notes: list[str],
    sources: list[RetrievedSource],
) -> str:
    """Combine all grounding text used for verification checks."""
    parts: list[str] = []
    parts.extend(notes or [])
    parts.extend(raw_notes or [])
    for src in sources or []:
        parts.append(f"URL: {src.url}")
        if src.title:
            parts.append(src.title)
        if src.citation:
            parts.append(src.citation)
        if src.excerpt:
            parts.append(src.excerpt)
    return "\n".join(parts)


_CASE_NAME_PATTERN = re.compile(
    r"\b[A-Z][A-Za-z0-9&\.\'\-\s]{2,60}\s+v\.?\s+(?:State\s+of\s+)?[A-Za-z][A-Za-z\s&\.\']{1,60}\b"
)

_INLINE_CITATION_PATTERN = re.compile(r"\[(\d+)\]")

_CITATION_PATTERNS = [
    # Neutral Supreme Court citation (e.g. "2024 INSC 101")
    r"\b\d{4}\s+INSC\s+\d+\b",
    # SCC Online with court code (e.g. "2023 SCC OnLine SC 1234")
    r"\b\d{4}\s+SCC\s+OnLine\s+[A-Z][A-Za-z]*\s+\d+\b",
    # SCC reporter (e.g. "(2019) 5 SCC 1")
    r"\(\d{4}\)\s+\d+\s+SCC\s+\d+\b",
    # SCC Criminal (e.g. "(2020) 3 SCC (Cri) 45")
    r"\(\d{4}\)\s+\d+\s+SCC\s*\(Cri\)\s+\d+\b",
    # AIR Supreme Court (e.g. "AIR 2005 SC 1234")
    r"\bAIR\s+\d{4}\s+SC\s+\d+\b",
    # AIR High Court (e.g. "AIR 2010 All 56", "AIR 2015 Del 78")
    r"\bAIR\s+\d{4}\s+[A-Z][A-Za-z]{1,6}\s+\d+\b",
    # Indian Law Reports (e.g. "ILR 2020 Delhi 101", "ILR (2018) MP 35")
    r"\bILR\s+(?:\(\d{4}\)\s+|\d{4}\s+)[A-Z][A-Za-z]*\s+\d+\b",
    # Manupatra neutral citation (e.g. "MANU/SC/0123/2022", "MANU/UL/0045/2021")
    r"\bMANU/[A-Z]{2,4}/\d{4}/\d{4}\b",
    # Criminal Law Journal (e.g. "CriLJ 2019 SC 450", "(2020) CriLJ 1200")
    r"\bCri(?:minal\s+)?LJ\s+\d{4}\s+[A-Z][A-Za-z]*\s+\d+\b",
    r"\(\d{4}\)\s+Cri(?:minal\s+)?LJ\s+\d+\b",
    # Delhi Law Times (e.g. "(2022) 4 DLT 56")
    r"\(\d{4}\)\s+\d+\s+DLT\s+\d+\b",
    # All India Reporter High Court short forms (e.g. "AIR 2018 Utt 10")
    r"\bAIR\s+\d{4}\s+Utt(?:arakhand)?\s+\d+\b",
    # Neutral High Court citations e.g. "2023:UKHC:1234"
    r"\b\d{4}:[A-Z]{2,6}HC:\d+\b",
    # SCR (Supreme Court Reports) e.g. "(2021) 1 SCR 100"
    r"\(\d{4}\)\s+\d+\s+SCR\s+\d+\b",
]


def _normalize_citation(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().upper()


def extract_citations(text: str) -> list[str]:
    """Extract normalized case citations from text."""
    found: set[str] = set()
    for pattern in _CITATION_PATTERNS:
        for match in re.findall(pattern, text or "", flags=re.IGNORECASE):
            found.add(_normalize_citation(match))
    return sorted(found)


_NOISE_PREFIXES = (
    "AS HELD IN ",
    "AS PER ",
    "IN RE ",
    "SEE ",
    "PER ",
    "UNDER ",
    "ACCORDING TO ",
)


def extract_case_names(text: str) -> list[str]:
    """Extract normalized case names from text."""
    found: set[str] = set()
    for match in _CASE_NAME_PATTERN.findall(text or ""):
        normalized = re.sub(r"\s+", " ", match).strip().upper()
        for prefix in _NOISE_PREFIXES:
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix) :].strip()
        if len(normalized) > 8 and " V " in normalized:
            found.add(normalized)
    return sorted(found)


def extract_inline_citation_numbers(text: str) -> list[int]:
    """Extract inline [n] citation numbers from memo text."""
    return sorted({int(m) for m in _INLINE_CITATION_PATTERN.findall(text or "")})


def extract_sources_section_urls(text: str) -> dict[int, str]:
    """Parse ### Sources section mapping [n] to URL."""
    mapping: dict[int, str] = {}
    in_sources = False
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("### sources"):
            in_sources = True
            continue
        if in_sources and stripped.startswith("## ") and not stripped.lower().startswith("### sources"):
            break
        match = re.match(r"^\[(\d+)\].*?(https?://\S+)", stripped)
        if match:
            mapping[int(match.group(1))] = match.group(2).rstrip(").,")
    return mapping


_REDACTED_CITATION = "[CITATION REMOVED — not in retrieved sources]"


def redact_fabricated_citations(report: str, fabricated: list[str]) -> str:
    """Replace fabricated citations with a visible redaction marker."""
    if not fabricated:
        return report

    result = report or ""
    for citation in fabricated:
        parts = citation.strip().split()
        if not parts:
            continue
        pattern = r"\s+".join(re.escape(part) for part in parts)
        result = re.sub(pattern, _REDACTED_CITATION, result, flags=re.IGNORECASE)
    return result
