"""Obligation retrieval relevance — taxonomy categories (Phase OR-1)."""

from __future__ import annotations

from document_core.schemas.chunk import IndexedChunk
from document_core.schemas.taxonomy import normalize_categories
from review_agent.config import ReviewSettings, get_settings
from review_agent.schemas.obligation import ContractObligation
from review_agent.schemas.routing_plan import ObligationRoutingPlan
from review_agent.services.section_category_lexical import infer_lexical_classify


def _legacy_concept_categories(
    plan: ObligationRoutingPlan,
    obligation: ContractObligation,
) -> list[str]:
    cats = [c for c in plan.concepts if c and c != "general"]
    obligation_type = (obligation.obligation_type or "").strip()
    if obligation_type and obligation_type not in ("general", "boilerplate"):
        cats.append(obligation_type)
    return cats or list(plan.concepts)


def obligation_relevance_categories(
    *,
    plan: ObligationRoutingPlan,
    obligation: ContractObligation,
    section: IndexedChunk | None,
    settings: ReviewSettings | None = None,
) -> tuple[list[str], str]:
    """Return taxonomy categories and source tag for obligation relevance filter."""
    cfg = settings or get_settings()
    if not cfg.obligation_relevance_use_lexical_categories:
        return _legacy_concept_categories(plan, obligation), "concepts"

    cats: list[str] = []
    if section is not None:
        lexical = infer_lexical_classify(
            section,
            context_text=(obligation.text or "")[:2000],
        )
        cats.extend(lexical.categories)
    if obligation.obligation_type:
        cats.extend(normalize_categories([obligation.obligation_type]))
    normalized = list(dict.fromkeys(normalize_categories(cats)))
    if normalized:
        return normalized, "taxonomy"
    return ["general"], "general_fallback"
