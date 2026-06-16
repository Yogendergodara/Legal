"""Post-processing helpers to ensure citation quality in delivered memoranda."""

from __future__ import annotations

import re

from deep_research_from_scratch.source_registry import RetrievedSource, filter_citable_sources


def build_case_digest(sources: list[RetrievedSource]) -> str:
    """Structured per-case digest so the writer analyzes every fetched judgment."""
    fetched = [
        s for s in filter_citable_sources(sources) if s.fetched and s.url
    ]
    if not fetched:
        return "(No case digest — no fetched primary sources. Do not invent holdings.)"

    lines = [
        "## Case Digest — analyze EACH fetched source below in Discussion",
        "Use the full URL exactly as shown. Do not truncate URLs.",
        "",
    ]
    for index, src in enumerate(fetched, 1):
        lines.append(f"### [{index}] {src.title}")
        lines.append(f"Full URL: {src.url}")
        lines.append(f"Status: FETCHED | Tier: {src.authority_tier}")
        if src.citation:
            lines.append(f"Citation: {src.citation}")
        if src.excerpt:
            lines.append(f"Key text from source:\n{src.excerpt[:1500]}")
        lines.append("")
    return "\n".join(lines).rstrip()


def ensure_sources_section(report: str, sources: list[RetrievedSource]) -> str:
    """Ensure ### Sources lists every fetched registry entry with full https URLs."""
    entries: list[str] = []
    fetched = [
        s
        for s in filter_citable_sources(sources)
        if s.fetched and (s.url or "").startswith("http")
    ]
    for index, src in enumerate(fetched or sources, 1):
        url = (src.url or "").strip()
        if not url.startswith("http"):
            continue
        title = (src.title or "Source").strip()
        entries.append(f"[{index}] {title}: {url}")

    if not entries:
        return report

    text = report or ""
    text = re.sub(
        r"\n### Sources\s*[\s\S]*?(?=\n## |\Z)",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return text.rstrip() + "\n\n### Sources\n" + "\n".join(entries)
