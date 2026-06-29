"""Post-compare guard — downgrade false gaps when policy family ≠ section topic (Phase I)."""

from __future__ import annotations

from document_core.schemas.chunk import IndexedChunk, RetrievalHit
from document_core.schemas.compliance import ComplianceStatus, Severity
from document_core.schemas.taxonomy import BROAD_POLICY_CATEGORIES, normalize_categories
from review_agent.schemas.section_compare import SectionCompareItem
from review_agent.services.retrieval_relevance import (
    has_specific_category_overlap,
    is_incompatible_hit,
)

_SUFFIX = " (Topic mismatch — policy family does not apply to this section.)"


def _section_has_specific_categories(categories: list[str]) -> bool:
    return bool(
        {c for c in normalize_categories(categories) if c not in BROAD_POLICY_CATEGORIES}
    )


def _resolve_hit(
    item: SectionCompareItem,
    hits_by_section: dict[str, list[RetrievalHit]],
) -> RetrievalHit | None:
    hits = hits_by_section.get(item.section_id) or []
    if not hits:
        return None
    if item.policy_document_id or item.policy_section_id:
        for hit in hits:
            parent = hit.parent_chunk
            if item.policy_document_id and str(parent.document_id) != item.policy_document_id:
                continue
            if item.policy_section_id and parent.section_id != item.policy_section_id:
                continue
            return hit
    return hits[0]


def _is_topic_mismatch(
    section_categories: list[str],
    section_title: str,
    hit: RetrievalHit,
    *,
    doc_catalog_categories: dict[str, list[str]] | None = None,
) -> bool:
    if is_incompatible_hit(
        section_categories,
        section_title,
        hit,
        doc_catalog_categories=doc_catalog_categories,
    ):
        return True
    if _section_has_specific_categories(section_categories) and not has_specific_category_overlap(
        section_categories,
        hit,
        doc_catalog_categories=doc_catalog_categories,
    ):
        return True
    return False


def apply_topic_mismatch_guard(
    items: list[SectionCompareItem],
    *,
    sections_by_id: dict[str, IndexedChunk],
    categories_by_section: dict[str, list[str]],
    hits_by_section: dict[str, list[RetrievalHit]],
    doc_catalog_categories: dict[str, list[str]] | None = None,
) -> tuple[list[SectionCompareItem], int]:
    """Downgrade false NON_COMPLIANT / INCONCLUSIVE when paired policy is off-topic."""
    downgraded = 0
    result: list[SectionCompareItem] = []
    for item in items:
        if item.status not in (ComplianceStatus.NON_COMPLIANT, ComplianceStatus.INCONCLUSIVE):
            result.append(item)
            continue
        section = sections_by_id.get(item.section_id)
        section_title = (section.title if section else None) or item.section_id
        section_categories = categories_by_section.get(item.section_id, [])
        hit = _resolve_hit(item, hits_by_section)
        if hit is None or not _is_topic_mismatch(
            section_categories,
            section_title,
            hit,
            doc_catalog_categories=doc_catalog_categories,
        ):
            result.append(item)
            continue
        rationale = item.rationale
        if _SUFFIX not in rationale:
            rationale = f"{rationale}{_SUFFIX}"
        result.append(
            item.model_copy(
                update={
                    "status": ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
                    "severity": Severity.INFO,
                    "policy_quote": "",
                    "rationale": rationale,
                    "confidence": 0.9,
                }
            )
        )
        downgraded += 1
    return result, downgraded
