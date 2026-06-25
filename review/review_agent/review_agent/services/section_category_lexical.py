"""Lexical-first section classification: categories + policy retrieval query terms."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from document_core.schemas.chunk import IndexedChunk
from document_core.schemas.taxonomy import normalize_categories

# (regex on title/body snippet, taxonomy category)
_CATEGORY_KEYWORDS: tuple[tuple[str, str], ...] = (
    (r"liabilit", "liability"),
    (r"indemn", "indemnity"),
    (r"confidential", "confidentiality"),
    (r"terminat", "termination"),
    (r"\bip\b|intellectual property|ownership", "ip"),
    (r"data\s+process|privacy|personal data|data protection", "privacy"),
    (r"data retention|retention period", "data_retention"),
    (r"governing law|jurisdiction", "governing_law"),
    (r"assign(ment|able)|transfer of (this )?agreement", "termination"),
    (r"\binsurance\b|certificate of insurance", "insurance"),
    (r"service level|\bsla\b", "sla"),
    (r"payment|invoice", "payment"),
    (r"security control|encryption|access control|master security|\bmss\b|supply chain security", "security"),
    (r"information security|cybersecurity|data security", "security"),
    (r"vendor security", "vendor_security"),
    (r"business continuity|\bbcp\b|supply chain visibility|\bscv\b", "vendor_security"),
    (r"risk management|operational risk|enterprise risk", "vendor_security"),
    (r"disaster recovery|\bdr\b plan|resilience", "vendor_security"),
    (r"subcontract|sub-contract|flow.?down", "procurement"),
    (r"audit rights|right to audit|books and records", "compliance"),
    (r"export control|anti.?corruption|\bfcpa\b", "compliance"),
    (r"non-compete|employment", "employment"),
    (r"procurement|sourcing", "procurement"),
    (r"\bai\b|automated decision|machine learning", "ai_usage"),
    (r"supplier code of conduct|code of conduct|\brba\b|social compliance|saq\b|vap audit", "compliance"),
    (
        r"human rights|forced labor|modern slavery|traffick|bonded labor|indentured labor|"
        r"freedom of association|un guiding principles|\bilo\b",
        "human_rights",
    ),
    (r"rights and labor|child labor|working hours|wage|recruitment agenc|labor standard", "labor"),
    (r"\bhr\b|human resources|employee benefit|leave polic|workplace conduct", "hr"),
    (
        r"responsible mineral|conflict mineral|\bmrt\b|\brmap\b|smelter|refiner|\b3tg\b|"
        r"tin.*tantalum|tungsten.*gold",
        "minerals",
    ),
    (r"\bghg\b|greenhouse gas|\bcdp\b|carbon emission|emissions reduction|climate target", "environment"),
    (r"sustainability|circular design|circular econom|environmental impact", "sustainability"),
    (r"secure delet|secure destruction|irreversibly delet", "secure_deletion"),
    (r"legal hold|litigation hold|preservation notice", "legal_hold"),
    (r"data subject rights|data principal|\bgdpr\b|\bdpdpa\b", "data_subject_rights"),
    (r"incident report|security incident|incident response", "incident_reporting"),
    (r"breach notif|notify.*breach", "breach_notification"),
    (r"trademark|logo usage|brand guideline", "trademark"),
    (r"anti.?brib|\bfcpa\b|kickback", "anti_bribery"),
    (r"\baml\b|money laundering|\bkyc\b", "aml"),
)

_CATEGORY_QUERY_TERMS: dict[str, tuple[str, ...]] = {
    "liability": ("limitation of liability cap",),
    "indemnity": ("indemnification obligations",),
    "confidentiality": ("confidential information", "confidentiality period"),
    "termination": ("termination notice period", "term and survival"),
    "ip": ("intellectual property ownership",),
    "privacy": ("data protection personal data",),
    "data_retention": ("data retention and deletion",),
    "governing_law": ("governing law and jurisdiction",),
    "insurance": ("insurance requirements",),
    "sla": ("service level agreement",),
    "payment": ("payment terms and invoicing",),
    "security": (
        "information security controls",
        "master security specification MSS",
    ),
    "vendor_security": (
        "vendor security assessment",
        "business continuity SCV",
        "supply chain visibility",
    ),
    "employment": ("employment and non-compete",),
    "hr": ("workplace conduct policy",),
    "procurement": ("procurement and sourcing",),
    "ai_usage": ("AI and automated decision making",),
    "human_rights": ("forced labor human rights", "supplier human rights"),
    "labor": ("working conditions labor standards",),
    "minerals": ("conflict minerals MRT RMAP",),
    "environment": ("greenhouse gas emissions CDP",),
    "sustainability": ("sustainability reporting",),
    "compliance": ("supplier code of conduct",),
}

_MAX_INFERRED_CATEGORIES = 3
_DEFAULT_BODY_SCAN_CHARS = 800
_DEFAULT_FULL_BODY_MAX_CHARS = 4000


@dataclass(frozen=True)
class LexicalClassifyResult:
    categories: list[str]
    confidence: Literal["title", "body", "none"]
    matched_via: str


def _scan_settings(
    body_scan_chars: int | None,
    full_body_max_chars: int | None,
) -> tuple[int, int]:
    if body_scan_chars is not None and full_body_max_chars is not None:
        return body_scan_chars, full_body_max_chars
    try:
        from review_agent.config import get_settings

        cfg = get_settings()
        return (
            body_scan_chars if body_scan_chars is not None else cfg.section_lexical_body_scan_chars,
            full_body_max_chars
            if full_body_max_chars is not None
            else cfg.section_lexical_full_body_max_chars,
        )
    except Exception:  # noqa: BLE001
        return (
            body_scan_chars if body_scan_chars is not None else _DEFAULT_BODY_SCAN_CHARS,
            full_body_max_chars if full_body_max_chars is not None else _DEFAULT_FULL_BODY_MAX_CHARS,
        )


def _scan_text(text: str, *, title_priority: bool) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for pattern, category in _CATEGORY_KEYWORDS:
        if category in seen:
            continue
        if re.search(pattern, text, re.IGNORECASE):
            seen.add(category)
            found.append(category)
            if len(found) >= _MAX_INFERRED_CATEGORIES:
                break
    if title_priority and found:
        return found
    return found


def infer_lexical_classify(
    section: IndexedChunk,
    *,
    context_text: str = "",
    body_scan_chars: int | None = None,
    full_body_max_chars: int | None = None,
) -> LexicalClassifyResult:
    """Derive taxonomy categories with confidence from title, body tiers, and cross-ref context."""
    scan_chars, full_max = _scan_settings(body_scan_chars, full_body_max_chars)
    title = (section.title or section.section_id or "").strip()
    body = (section.text or "").strip()
    context = (context_text or "").strip()

    if title:
        from_title = _scan_text(title, title_priority=True)
        if from_title:
            categories = normalize_categories(from_title)
            if categories:
                return LexicalClassifyResult(
                    categories=categories,
                    confidence="title",
                    matched_via="title",
                )

    def _body_scan(snippet: str) -> list[str]:
        combined = f"{title} {context} {snippet}".strip()
        if not combined:
            return []
        return normalize_categories(_scan_text(combined, title_priority=False))

    from_partial = _body_scan(body[:scan_chars])
    if from_partial:
        return LexicalClassifyResult(
            categories=from_partial,
            confidence="body",
            matched_via="body",
        )

    if body and len(body) <= full_max:
        from_full = _body_scan(body)
        if from_full:
            return LexicalClassifyResult(
                categories=from_full,
                confidence="body",
                matched_via="body_full",
            )

    return LexicalClassifyResult(categories=[], confidence="none", matched_via="")


def infer_categories_from_section(
    section: IndexedChunk,
    *,
    context_text: str = "",
) -> list[str]:
    """Derive taxonomy categories from section title and body when LLM classify fails."""
    return infer_lexical_classify(section, context_text=context_text).categories


def _contract_snippet_query(section: IndexedChunk) -> str:
    title = (section.title or section.section_id or "").strip()
    body = (section.text or "").strip()
    snippet = " ".join(body.split()[:24])
    if title and snippet:
        return f"{title} {snippet}"
    return title or snippet


def infer_query_terms_from_lexical(
    categories: list[str],
    section: IndexedChunk,
) -> list[str]:
    """Policy-oriented retrieval phrases aligned with taxonomy categories."""
    terms: list[str] = []
    for cat in categories[:3]:
        for phrase in _CATEGORY_QUERY_TERMS.get(cat, ()):
            if phrase not in terms:
                terms.append(phrase)
            if len(terms) >= 3:
                return terms
    title = (section.title or "").strip()
    if title:
        return [title]
    return [_contract_snippet_query(section)]
