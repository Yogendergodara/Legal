"""Policy category taxonomy for metadata-filtered retrieval."""

from __future__ import annotations

# Standard policy families (extend via ingest metadata.categories).
STANDARD_POLICY_CATEGORIES: frozenset[str] = frozenset(
    {
        "security",
        "vendor_security",
        "privacy",
        "data_retention",
        "confidentiality",
        "indemnity",
        "liability",
        "termination",
        "ip",
        "employment",
        "hr",
        "procurement",
        "ai_usage",
        "governing_law",
        "payment",
        "sla",
        "insurance",
        "minerals",
        "human_rights",
        "labor",
        "compliance",
        "environment",
        "sustainability",
        "general",
        # Phase D — specific tags (P0 + P1)
        "secure_deletion",
        "legal_hold",
        "data_subject_rights",
        "incident_reporting",
        "breach_notification",
        "trademark",
        "forced_labor",
        "modern_slavery",
        "anti_bribery",
        "aml",
        "cross_border_transfer",
        "vendor_due_diligence",
        "access_control",
        "encryption",
        "audit_rights",
        "whistleblower",
        "records_management",
        "business_continuity",
        "export_control",
        "sanctions",
    }
)

BROAD_POLICY_CATEGORIES: frozenset[str] = frozenset({"general", "compliance", "security"})


# Java sync / playbook labels that differ from taxonomy canonical names.
_CATEGORY_ALIASES: dict[str, str] = {
    "indemnification": "indemnity",
    "indemnify": "indemnity",
    "hold_harmless": "indemnity",
    "data_protection": "privacy",
    "limitation_of_liability": "liability",
    "limitation_of_liability_cap": "liability",
    "confidential_information": "confidentiality",
    "intellectual_property": "ip",
    "governing_law_and_jurisdiction": "governing_law",
    "esg": "environment",
    "responsible_minerals": "minerals",
    "conflict_minerals": "minerals",
    "ghg": "environment",
    "climate": "environment",
    "code_of_conduct": "compliance",
    "gdpr": "data_subject_rights",
    "dpdpa": "data_subject_rights",
    "incident_response": "incident_reporting",
    "logo_usage": "trademark",
    "anti_corruption": "anti_bribery",
}


def _canonical_category(key: str) -> str:
    return _CATEGORY_ALIASES.get(key, key)


def category_aliases() -> dict[str, str]:
    """Alias map for UI display and ingest hints (Java sync labels → canonical)."""
    return dict(_CATEGORY_ALIASES)


def normalize_categories(raw: list[str] | None) -> list[str]:
    """Lowercase, alias, dedupe, drop empty category tags."""
    if not raw:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        key = (item or "").strip().lower().replace(" ", "_")
        if not key:
            continue
        key = _canonical_category(key)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def cap_section_categories(
    categories: list[str],
    *,
    max_tags: int = 3,
    broad: frozenset[str] | None = None,
) -> list[str]:
    """Keep most specific tags; drop broad magnets when specifics exist."""
    broad_set = broad or BROAD_POLICY_CATEGORIES
    norm = normalize_categories(categories)
    specific = [c for c in norm if c not in broad_set]
    broad_only = [c for c in norm if c in broad_set]
    if specific:
        return specific[:max_tags]
    return (broad_only or ["general"])[:max_tags]


def taxonomy_prompt_labels() -> str:
    """Comma-separated allowed category labels for LLM prompts (excludes general)."""
    return ", ".join(sorted(STANDARD_POLICY_CATEGORIES - {"general"}))
