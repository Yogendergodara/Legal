"""Ingest-time policy category inference (document_core only)."""

from __future__ import annotations

import re

from document_core.schemas.taxonomy import STANDARD_POLICY_CATEGORIES, normalize_categories

_BOUNDARY_TOKENS = frozenset({"sla", "ip", "hr", "ai", "dr", "mss", "aml"})

# Regex patterns checked before plain phrases (more specific first).
_CATEGORY_REGEX: tuple[tuple[str, str], ...] = (
    (r"modern slavery", "modern_slavery"),
    (r"forced labor|forced labour", "forced_labor"),
    (r"human rights", "human_rights"),
    (r"\bsla\b|service level agreement", "sla"),
    (
        r"\binformation security\b|\bcybersecurity\b|\bsecurity control\b|"
        r"\bdata security\b",
        "security",
    ),
    (r"\bunauthorized access\b|\bcryptomining\b|\bcrypto[\s-]?mining\b", "access_control"),
    (r"\bmalware\b|\bvirus\b|\bhacking\b|\bprohibited activ", "access_control"),
    (r"\bdmca\b|copyright infringement|circumvent.{0,40}copyright", "ip"),
    (r"\bsecurity incident\b|\bincident report", "incident_reporting"),
    (r"secure delet|secure destruction|irreversibly delet", "secure_deletion"),
    (r"legal hold|litigation hold", "legal_hold"),
    (r"data subject rights|data principal|\bgdpr\b|\bdpdpa\b", "data_subject_rights"),
    (r"incident response|incident report|security incident", "incident_reporting"),
    (r"breach notif|notify.*breach", "breach_notification"),
    (r"trademark|logo usage|logo/trademark", "trademark"),
    (r"anti.?brib|\bfcpa\b|kickback", "anti_bribery"),
    (r"\baml\b|money laundering", "aml"),
)

_CATEGORY_PHRASES: tuple[tuple[str, str], ...] = (
    ("code of conduct", "compliance"),
    ("code-of-conduct", "compliance"),
    ("limitation of liability", "liability"),
    ("limitation_of_liability", "liability"),
    ("confidential information", "confidentiality"),
    ("non-disclosure", "confidentiality"),
    ("non disclosure", "confidentiality"),
    ("indemnification", "indemnity"),
    ("indemnify", "indemnity"),
    ("hold harmless", "indemnity"),
    ("data protection", "privacy"),
    ("data retention", "data_retention"),
    ("governing law", "governing_law"),
    ("intellectual property", "ip"),
    ("termination", "termination"),
    ("confidentiality", "confidentiality"),
    ("confidential", "confidentiality"),
    ("liability", "liability"),
    ("indemnity", "indemnity"),
    ("privacy", "privacy"),
    ("insurance", "insurance"),
    ("payment", "payment"),
    ("procurement", "procurement"),
    ("employment", "employment"),
    ("compliance", "compliance"),
    ("conduct", "compliance"),
    ("logo guidelines", "trademark"),
    ("logo usage", "trademark"),
    ("acceptable use", "access_control"),
    ("prohibited use", "access_control"),
    ("misuse of", "access_control"),
    ("dmca", "ip"),
    ("malware", "access_control"),
    ("unauthorized access", "access_control"),
    ("harassment", "access_control"),
    ("spam", "access_control"),
    ("machine learning", "ai_usage"),
    ("generative ai", "ai_usage"),
)


def _phrase_matches(phrase: str, text: str) -> bool:
    if phrase in _BOUNDARY_TOKENS or len(phrase) <= 3:
        return bool(
            re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text, re.IGNORECASE)
        )
    return phrase in text


def _match_regex_patterns(text: str, seen: set[str], found: list[str]) -> None:
    for pattern, category in _CATEGORY_REGEX:
        if category in seen:
            continue
        if re.search(pattern, text, re.IGNORECASE):
            seen.add(category)
            found.append(category)


def _match_phrases(text: str, seen: set[str], found: list[str]) -> None:
    lowered = text.lower()
    for phrase, category in _CATEGORY_PHRASES:
        if category in seen:
            continue
        if _phrase_matches(phrase, lowered):
            seen.add(category)
            found.append(category)


def _match_title_tokens(title: str, seen: set[str], found: list[str]) -> None:
    for token in re.split(r"[^a-z0-9]+", title.lower()):
        if not token or token in seen:
            continue
        if not re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", title.lower()):
            continue
        for cat in normalize_categories([token]):
            if cat in STANDARD_POLICY_CATEGORIES and cat != "general" and cat not in seen:
                seen.add(cat)
                found.append(cat)


def infer_section_categories_keyword(*, title: str, text: str) -> list[str]:
    """Per-section keyword/phrase infer; returns 1+ categories or ['general']."""
    title_hay = (title or "").strip()
    body_hay = (text or "")[:2000]
    found: list[str] = []
    seen: set[str] = set()

    combined = f"{title_hay} {body_hay}".strip()
    if not combined:
        return ["general"]

    _match_regex_patterns(title_hay, seen, found)
    _match_phrases(title_hay, seen, found)
    _match_title_tokens(title_hay, seen, found)

    _match_regex_patterns(body_hay, seen, found)
    _match_phrases(body_hay, seen, found)

    return found or ["general"]


def _infer_categories(*, title: str, section_texts: list[str]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    # Title-only ingest (document-level) must still infer from section title.
    bodies = section_texts[:3] if section_texts else [""]
    for section_text in bodies:
        for cat in infer_section_categories_keyword(title=title, text=section_text):
            if cat not in seen:
                seen.add(cat)
                found.append(cat)
    return found or ["general"]


def _explicit_categories(provided: list[str] | None, metadata: dict | None) -> list[str]:
    meta_raw = (metadata or {}).get("categories")
    meta_cats = normalize_categories(meta_raw if isinstance(meta_raw, list) else None)
    if meta_cats and meta_cats != ["general"]:
        return meta_cats
    norm_provided = normalize_categories(provided)
    if norm_provided and norm_provided != ["general"]:
        return norm_provided
    return []


def resolve_ingest_categories(
    *,
    title: str,
    section_texts: list[str],
    provided: list[str] | None,
    metadata: dict | None,
) -> tuple[list[str], dict[str, object]]:
    """Return resolved categories and extra metadata fields for ingest."""
    explicit = _explicit_categories(provided, metadata)
    if explicit:
        return explicit, {}

    inferred = _infer_categories(title=title, section_texts=section_texts)
    extra: dict[str, object] = {"auto_tagged": True}
    return inferred, extra
