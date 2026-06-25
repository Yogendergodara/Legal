"""Resolve contract policy name references to indexed document IDs."""

from __future__ import annotations

import re

# (phrase in contract text, substring to match in policy title)
_NAMED_POLICY_PHRASES: tuple[tuple[str, str], ...] = (
    ("security practices policy", "security practice"),
    ("data retention policy", "data retention"),
    ("privacy policy", "privacy"),
    ("code of conduct", "code of conduct"),
    ("incident response plan", "incident response"),
    ("incident response", "incident response"),
    ("terms of service", "terms of service"),
    ("trademark usage", "trademark"),
    ("logo/trademark", "trademark"),
    ("logo usage", "trademark"),
)

_POLICY_REF_RE = re.compile(
    r"(?i)(?:[\w'&-]+\s+)?("
    r"security\s+practices\s+policy|data\s+retention\s+policy|privacy\s+policy|"
    r"code\s+of\s+conduct|incident\s+response(?:\s+plan)?|terms\s+of\s+service|"
    r"logo/?trademark\s+usage\s+guidelines?"
    r")"
)


def extract_named_policy_title_keys(text: str) -> list[str]:
    """Return normalized title-match keys from explicit policy references in contract text."""
    haystack = (text or "").lower()
    keys: list[str] = []
    seen: set[str] = set()
    for match in _POLICY_REF_RE.finditer(haystack):
        phrase = match.group(1).strip().lower()
        if phrase and phrase not in seen:
            seen.add(phrase)
            keys.append(phrase)
    for phrase, title_key in _NAMED_POLICY_PHRASES:
        if phrase in haystack and title_key not in seen:
            seen.add(title_key)
            keys.append(title_key)
    return keys


def resolve_named_policy_doc_ids(
    title_keys: list[str],
    policy_catalog: list[dict],
) -> list[str]:
    """Map extracted keys to document_id values from discovery/index catalog."""
    if not title_keys or not policy_catalog:
        return []
    matched: list[str] = []
    seen: set[str] = set()
    for entry in policy_catalog:
        doc_id = str(entry.get("document_id") or "").strip()
        if not doc_id:
            continue
        title = (entry.get("title") or "").lower()
        if not title:
            continue
        for key in title_keys:
            if key in title or title in key:
                if doc_id not in seen:
                    seen.add(doc_id)
                    matched.append(doc_id)
                break
    return matched
