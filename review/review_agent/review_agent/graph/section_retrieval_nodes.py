"""Phase 10 section policy retrieval graph node."""

from __future__ import annotations

import logging
from typing import Any

from document_core.config import get_settings as get_core_settings
from review_agent.clients.document_client import DocumentMCPClient
from review_agent.config import get_settings
from review_agent.errors import FatalPipelineError
from review_agent.resilience.failed_sections import (
    classify_degraded_entries,
    retrieval_failed_entry,
    zero_hit_failed_entry,
)
from review_agent.schemas.section_retrieval import SectionRetrievalBundle
from review_agent.services.async_limits import gather_limited
from review_agent.services.multi_retrieval import multi_retrieve_for_section
from review_agent.services.section_classifier import classify_all_sections
from review_agent.services.section_cross_reference import resolve_all_related_sections
from review_agent.services.section_filter import filter_review_sections
from review_agent.state.review_state import ReviewState

logger = logging.getLogger(__name__)


async def section_policy_retrieval_node(
    state: ReviewState,
    client: DocumentMCPClient,
) -> dict[str, Any]:
    settings = get_settings()
    core = get_core_settings()
    sections = filter_review_sections(
        state.get("contract_sections") or [],
        min_chars=settings.review_min_section_chars,
    )
    scope_ids = list(
        state.get("policy_document_ids")
        or state.get("discovered_policy_document_ids")
        or []
    )
    classifications, classify_stats = await classify_all_sections(
        sections,
        contract_type=state.get("contract_type"),
        settings=settings,
    )

    related_bundles = (
        resolve_all_related_sections(sections, settings=settings)
        if settings.section_cross_ref_enabled
        else {}
    )
    context_serialized = {
        sid: {
            "primary_section_id": bundle.primary_section_id,
            "related": [
                {"section_id": r[0], "title": r[1], "excerpt": r[2]}
                for r in bundle.related
            ],
            "resolution_reason": bundle.resolution_reason,
        }
        for sid, bundle in related_bundles.items()
    }

    warnings: list[str] = []
    failed_sections: list[dict[str, str]] = classify_degraded_entries(classifications)
    boilerplate_skipped = 0
    general_blocked = 0
    cross_ref_sections = 0

    for section in sections:
        classification = classifications.get(section.section_id)
        if classification is None:
            continue
        if not classification.substantive:
            boilerplate_skipped += 1
        if classification.classify_warning and "substantive_title_lexical" in (
            classification.classify_warning or ""
        ):
            general_blocked += 1
        bundle = related_bundles.get(section.section_id)
        if bundle and bundle.related:
            cross_ref_sections += 1
        if classification.classify_warning:
            label = "classifier note"
            if classification.categories == ["general"] or "fallback" in (
                classification.classify_warning or ""
            ).lower():
                label = "classifier fallback"
            warnings.append(
                f"section {section.section_id} {label} (categories="
                f"{classification.categories}): {classification.classify_warning}"
            )

    coros = []
    for section in sections:
        classification = classifications.get(section.section_id)
        if classification is not None and not classification.substantive:
            coros.append(None)
            continue
        coros.append(
            multi_retrieve_for_section(
                client,
                tenant_id=state["tenant_id"],
                section=section,
                contract_type=state.get("contract_type"),
                policy_type=state.get("policy_type"),
                settings=settings,
                classification=classification,
                scope_document_ids=scope_ids or None,
                contract_routing=state.get("contract_routing"),
                policy_catalog=list(state.get("indexed_policies") or []),
            )
        )

    active_coros = [c for c in coros if c is not None]
    active_results = await gather_limited(active_coros, limit=settings.section_retrieval_concurrency)
    result_iter = iter(active_results)

    bundles: dict[str, SectionRetrievalBundle] = {}
    for section, coro in zip(sections, coros, strict=True):
        classification = classifications.get(section.section_id)
        if coro is None:
            bundles[section.section_id] = SectionRetrievalBundle(
                section_id=section.section_id,
                categories=(classification.categories if classification else ["general"]),
                policy_hits=[],
                retrieval_meta={
                    "skipped_reason": "boilerplate",
                    "substantive": False,
                },
            )
            continue
        result = next(result_iter)
        if isinstance(result, BaseException):
            if isinstance(result, FatalPipelineError):
                raise result
            msg = str(result)
            warnings.append(f"retrieval failed for section {section.section_id}: {result}")
            failed_sections.append(retrieval_failed_entry(section.section_id, msg))
            bundles[section.section_id] = SectionRetrievalBundle(
                section_id=section.section_id,
                categories=["general"],
                policy_hits=[],
                retrieval_meta={"error": str(result)},
            )
            continue
        meta = dict(result.retrieval_meta or {})
        meta["substantive"] = True
        bundle = related_bundles.get(section.section_id)
        if bundle and bundle.resolution_reason:
            meta["cross_ref_reason"] = bundle.resolution_reason
        bundles[section.section_id] = result.model_copy(update={"retrieval_meta": meta})

    serialized = {k: v.model_dump(mode="json") for k, v in bundles.items()}
    path_totals = {"dense": 0, "fts": 0, "metadata": 0}
    retry_sections = 0
    zero_hit_sections = 0
    max_attempts_used = 0
    reranker_cross_encoder_sections = 0
    reranker_lexical_fallback_sections = 0
    reranker_off_sections = 0
    category_filter_blocked = 0
    scope_fallback_sections = 0
    retry_recovered_sections = 0
    for section_id, bundle in bundles.items():
        meta = bundle.retrieval_meta or {}
        path_totals["dense"] += int(meta.get("dense_count") or 0)
        path_totals["fts"] += int(meta.get("fts_count") or 0)
        path_totals["metadata"] += int(meta.get("metadata_count") or 0)
        attempts = meta.get("attempts") or []
        if len(attempts) > 1:
            retry_sections += 1
        if meta.get("skipped_reason") == "boilerplate":
            continue
        if meta.get("category_filter_miss") or meta.get("category_filter_skipped") == (
            "scope_fallback_on_category_miss"
        ):
            category_filter_blocked += 1
        if meta.get("category_filter_skipped") == "scope_fallback_on_category_miss":
            scope_fallback_sections += 1
        if not bundle.policy_hits:
            zero_hit_sections += 1
            failed_sections.append(zero_hit_failed_entry(section_id))
        if attempts:
            max_attempts_used = max(max_attempts_used, len(attempts))
            if (
                len(attempts) > 1
                and bundle.policy_hits
                and int(attempts[0].get("final_count") or 0) == 0
            ):
                retry_recovered_sections += 1
        used = meta.get("reranker_used")
        if used == "cross_encoder":
            reranker_cross_encoder_sections += 1
        elif used in ("lexical_fallback", "lexical"):
            reranker_lexical_fallback_sections += 1
        elif used == "off" or meta.get("reranker_backend") == "off" or not core.reranker_enabled:
            reranker_off_sections += 1

    reranker_backend_config = core.reranker_backend if core.reranker_enabled else "off"

    if zero_hit_sections > 0:
        logger.warning(
            "retrieval_zero_hit_sections=%s tenant review may miss policy matches",
            zero_hit_sections,
        )

    return {
        "section_retrieval_by_id": serialized,
        "section_review_sections": [s.model_dump(mode="json") for s in sections],
        "section_context_by_id": context_serialized,
        "warnings": warnings,
        "failed_sections": failed_sections,
        "compliance_stats": {
            **dict(state.get("compliance_stats") or {}),
            **classify_stats,
            "sections_retrieved": len(bundles),
            "classify_boilerplate_skipped": boilerplate_skipped,
            "classify_general_blocked": general_blocked,
            "cross_ref_sections": cross_ref_sections,
            "retrieval_path_hits": path_totals,
            "retrieval_retry_sections": retry_sections,
            "retrieval_zero_hit_sections": zero_hit_sections,
            "retrieval_max_attempts_used": max_attempts_used,
            "retrieval_category_filter_blocked": category_filter_blocked,
            "retrieval_scope_fallback_sections": scope_fallback_sections,
            "retrieval_retry_recovered_sections": retry_recovered_sections,
            "reranker_cross_encoder_sections": reranker_cross_encoder_sections,
            "reranker_lexical_fallback_sections": reranker_lexical_fallback_sections,
            "reranker_off_sections": reranker_off_sections,
            "reranker_backend_config": reranker_backend_config,
        },
    }
