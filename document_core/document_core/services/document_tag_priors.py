"""Document-level tag priors and sync-time tag quality checks (Phase D)."""

from __future__ import annotations

from dataclasses import dataclass

from document_core.schemas.taxonomy import BROAD_POLICY_CATEGORIES, normalize_categories


@dataclass(frozen=True)
class DocumentTagPrior:
    title_keys: tuple[str, ...]
    prefer: tuple[str, ...]
    suppress: frozenset[str]


_DOCUMENT_PRIORS: tuple[DocumentTagPrior, ...] = (
    DocumentTagPrior(
        ("code of conduct",),
        ("human_rights", "compliance"),
        frozenset({"sla", "employment", "payment", "security"}),
    ),
    DocumentTagPrior(
        ("logo", "trademark"),
        ("trademark", "ip"),
        frozenset({"security", "compliance", "general"}),
    ),
    DocumentTagPrior(
        ("terms of service", "terms of use"),
        ("governing_law",),
        frozenset({"employment", "sla", "hr"}),
    ),
    DocumentTagPrior(
        ("data retention",),
        ("data_retention", "secure_deletion"),
        frozenset({"employment", "ip", "security"}),
    ),
    DocumentTagPrior(
        ("incident response",),
        ("incident_reporting", "security"),
        frozenset({"sla", "hr", "general"}),
    ),
    DocumentTagPrior(
        ("privacy",),
        ("privacy", "data_subject_rights"),
        frozenset({"employment", "payment"}),
    ),
    DocumentTagPrior(
        ("security practice",),
        ("security", "access_control"),
        frozenset({"compliance", "general", "ip"}),
    ),
    DocumentTagPrior(
        ("data processing", "dpa"),
        ("privacy", "cross_border_transfer"),
        frozenset({"employment", "payment"}),
    ),
    DocumentTagPrior(
        ("acceptable use", "aup"),
        ("compliance", "security"),
        frozenset({"sla", "payment"}),
    ),
    DocumentTagPrior(
        ("ai terms", "artificial intelligence"),
        ("ai_usage", "ip"),
        frozenset({"employment", "hr"}),
    ),
)


def resolve_document_prior(document_title: str) -> DocumentTagPrior | None:
    lowered = (document_title or "").lower()
    for prior in _DOCUMENT_PRIORS:
        if any(key in lowered for key in prior.title_keys):
            return prior
    return None


def apply_document_priors(categories: list[str], *, document_title: str) -> list[str]:
    prior = resolve_document_prior(document_title)
    norm = normalize_categories(categories)
    if prior is None:
        return norm
    kept = [cat for cat in norm if cat not in prior.suppress]
    for pref in prior.prefer:
        if pref not in kept:
            kept.append(pref)
    return kept


def document_prior_hint(document_title: str) -> str:
    prior = resolve_document_prior(document_title)
    if prior is None:
        return ""
    prefer = ", ".join(prior.prefer)
    suppress = ", ".join(sorted(prior.suppress))
    return (
        f"Document family hint: prefer {prefer}; "
        f"do NOT use {suppress} unless the section explicitly requires them."
    )


def assess_policy_tag_quality(
    *,
    document_title: str,
    section_categories: list[list[str]],
    tagger: str,
    document_union: list[str] | None = None,
) -> list[str]:
    """Return ingest/sync warnings for weak or polluted policy tags."""
    warnings: list[str] = []
    if tagger == "keyword":
        warnings.append("tagger=keyword; re-sync with CATEGORY_TAGGER_MODE=llm recommended")

    union = set(normalize_categories(document_union or []))
    if not union:
        for cats in section_categories:
            union.update(normalize_categories(cats))

    specific = union - BROAD_POLICY_CATEGORIES
    if not specific or union <= BROAD_POLICY_CATEGORIES:
        warnings.append("weak_tags: only broad categories (general/compliance/security)")

    prior = resolve_document_prior(document_title)
    if prior is not None and union:
        unexpected = sorted(union & prior.suppress)
        if unexpected:
            warnings.append(f"unexpected_tags:{','.join(unexpected)}")

    return warnings
