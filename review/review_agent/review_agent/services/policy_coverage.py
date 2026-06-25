"""Pre-compare policy coverage gate — block compare when evidence is off-topic."""

from __future__ import annotations

from dataclasses import dataclass, field

from document_core.schemas.chunk import IndexedChunk, RetrievalHit
from document_core.schemas.compliance import ComplianceStatus, Severity
from review_agent.config import ReviewSettings, get_settings
from review_agent.schemas.section_compare import SectionCompareItem
from review_agent.services.retrieval_relevance import (
    coverage_block_reason,
    filter_on_topic_hits,
    score_hit_relevance,
)


@dataclass
class SectionCoverageResult:
    section_id: str
    relevant_hits: list[RetrievalHit] = field(default_factory=list)
    dropped_hits: list[RetrievalHit] = field(default_factory=list)
    coverage_score: float = 0.0
    insufficient: bool = False
    reason: str = ""


def _relevance_floor(cfg: ReviewSettings) -> float:
    return max(cfg.retrieval_relevance_min_score, cfg.compare_hit_min_relevance_score)


def validate_section_coverage(
    section: IndexedChunk,
    hits: list[RetrievalHit],
    *,
    section_categories: list[str],
    settings: ReviewSettings | None = None,
    doc_catalog_categories: dict[str, list[str]] | None = None,
) -> SectionCoverageResult:
    """Score whether retrieved policies match the contract section topic."""
    cfg = settings or get_settings()
    sid = section.section_id
    section_title = section.title or sid
    if not hits:
        return SectionCoverageResult(section_id=sid, coverage_score=0.0, insufficient=False)

    floor = _relevance_floor(cfg)
    relevant, dropped, block_reason = filter_on_topic_hits(
        hits,
        section_categories=section_categories,
        section_title=section_title,
        min_score=floor,
        doc_catalog_categories=doc_catalog_categories,
        keep_best_fallback=cfg.retrieval_relevance_keep_best_fallback,
        require_specific_overlap=cfg.policy_coverage_require_specific_overlap,
    )

    if not relevant:
        reason = block_reason or coverage_block_reason(
            section_categories,
            section_title,
            hits,
            doc_catalog_categories=doc_catalog_categories,
        )
        return SectionCoverageResult(
            section_id=sid,
            relevant_hits=[],
            dropped_hits=hits,
            coverage_score=0.0,
            insufficient=True,
            reason=reason,
        )

    score = len(relevant) / max(len(hits), 1)

    if score < cfg.policy_coverage_min_score and len(dropped) > 0:
        doc_ids = {str(h.parent_chunk.document_id) for h in hits}
        rel_doc_ids = {str(h.parent_chunk.document_id) for h in relevant}
        if len(doc_ids - rel_doc_ids) > 0:
            return SectionCoverageResult(
                section_id=sid,
                relevant_hits=relevant,
                dropped_hits=dropped,
                coverage_score=score,
                insufficient=True,
                reason="low_coverage_mixed_policies",
            )

    return SectionCoverageResult(
        section_id=sid,
        relevant_hits=relevant,
        dropped_hits=dropped,
        coverage_score=score,
        insufficient=False,
    )


def _ipc_item(section: IndexedChunk, result: SectionCoverageResult) -> SectionCompareItem:
    return SectionCompareItem(
        section_id=section.section_id,
        dimension_label=section.title or section.section_id,
        status=ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
        severity=Severity.INFO,
        contract_quote="",
        policy_quote="",
        rationale=(
            "Retrieved policies were not sufficiently on-topic for this contract section "
            f"(coverage={result.coverage_score:.2f}, reason={result.reason}). "
            "Compare skipped to avoid false gaps."
        ),
        confidence=0.85,
    )


def apply_coverage_gate(
    sections: list[IndexedChunk],
    hits_by_section: dict[str, list[RetrievalHit]],
    categories_by_section: dict[str, list[str]],
    *,
    settings: ReviewSettings | None = None,
    doc_catalog_categories: dict[str, list[str]] | None = None,
) -> tuple[dict[str, list[RetrievalHit]], list[SectionCompareItem], list[str]]:
    """Filter hits per section; emit IPC items when coverage is too low."""
    cfg = settings or get_settings()
    if not cfg.policy_coverage_enabled:
        return dict(hits_by_section), [], []

    filtered: dict[str, list[RetrievalHit]] = {}
    ipc_items: list[SectionCompareItem] = []
    warnings: list[str] = []

    for section in sections:
        sid = section.section_id
        hits = list(hits_by_section.get(sid) or [])
        if not hits:
            filtered[sid] = []
            continue
        result = validate_section_coverage(
            section,
            hits,
            section_categories=categories_by_section.get(sid, []),
            settings=cfg,
            doc_catalog_categories=doc_catalog_categories,
        )
        if result.insufficient:
            ipc_items.append(_ipc_item(section, result))
            filtered[sid] = []
            warnings.append(
                f"section {sid}: policy coverage gate ({result.reason}, score={result.coverage_score:.2f})"
            )
        else:
            filtered[sid] = result.relevant_hits
            if result.dropped_hits:
                warnings.append(
                    f"section {sid}: dropped {len(result.dropped_hits)} off-topic policy hit(s)"
                )

    return filtered, ipc_items, warnings


def catalog_doc_categories(policy_catalog: list[dict]) -> dict[str, list[str]]:
    from document_core.schemas.taxonomy import normalize_categories

    out: dict[str, list[str]] = {}
    for entry in policy_catalog or []:
        doc_id = str(entry.get("document_id") or "").strip()
        if not doc_id:
            continue
        cats = entry.get("categories")
        if isinstance(cats, list):
            out[doc_id] = normalize_categories([str(c) for c in cats])
    return out


def filter_doc_ids_by_category_overlap(
    doc_ids: list,
    *,
    section_categories: list[str],
    catalog_categories: dict[str, list[str]],
    min_overlap: int,
) -> list:
    """Keep policy documents with enough specific category overlap (B4)."""
    if min_overlap <= 0 or not doc_ids:
        return list(doc_ids)
    from review_agent.services.retrieval_relevance import _specific_categories

    section_specific = _specific_categories(section_categories)
    if not section_specific:
        return list(doc_ids)
    kept = []
    for doc_id in doc_ids:
        key = str(doc_id)
        policy_cats = _specific_categories(catalog_categories.get(key, []))
        if len(section_specific & policy_cats) >= min_overlap:
            kept.append(doc_id)
    return kept
