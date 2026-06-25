"""Phase 10 section-first compare, merge, and gap verify nodes."""

from __future__ import annotations

from typing import Any

from document_core.schemas.chunk import IndexedChunk
from review_agent.clients.document_client import DocumentMCPClient
from review_agent.config import get_settings
from review_agent.schemas.obligation import ContractObligation
from review_agent.schemas.section_retrieval import SectionRetrievalBundle
from review_agent.services.final_verify_llm import run_final_gap_verify
from review_agent.services.policy_coverage import apply_coverage_gate, catalog_doc_categories
from review_agent.services.section_compare_llm import compare_all_sections
from review_agent.services.playbook_context import build_playbook_hints_by_document
from review_agent.services.section_coverage import ensure_section_coverage, reviewable_sections
from review_agent.resilience.failed_sections import compare_failed_entries
from review_agent.services.section_merge import merge_section_findings
from review_agent.services.routing_tenant import obligation_routing_active
from review_agent.state.review_state import ReviewState


def _load_bundles(state: ReviewState) -> dict[str, SectionRetrievalBundle]:
    raw = state.get("section_retrieval_by_id") or {}
    return {
        key: SectionRetrievalBundle.model_validate(value)
        for key, value in raw.items()
    }


def _load_sections(state: ReviewState) -> list[IndexedChunk]:
    raw = state.get("section_review_sections") or []
    return [IndexedChunk.model_validate(item) for item in raw]


def _playbook_hints(state: ReviewState):
    return build_playbook_hints_by_document(state.get("indexed_policies"))


def _sections_for_legacy_compare(
    sections: list[IndexedChunk],
    state: ReviewState,
    settings,
) -> list[IndexedChunk]:
    tenant_id = str(state.get("tenant_id") or "")
    if not obligation_routing_active(tenant_id, settings) or not settings.obligation_compare_enabled:
        return sections
    if settings.obligation_section_cutover_mode != "skip":
        return sections
    covered = {
        ContractObligation.model_validate(item).section_id
        for item in (state.get("obligations") or [])
    }
    if not covered:
        return sections
    return [section for section in sections if section.section_id not in covered]


def _bundle_is_substantive(
    bundle: SectionRetrievalBundle | None,
    *,
    settings,
) -> bool:
    if not settings.gap_boilerplate_skip_compare:
        return True
    if bundle is None:
        return True
    meta = bundle.retrieval_meta or {}
    if meta.get("skipped_reason") == "boilerplate":
        return False
    return meta.get("substantive", True) is not False


async def section_compare_llm_node(
    state: ReviewState,
    client: DocumentMCPClient,
) -> dict[str, Any]:
    _ = client
    settings = get_settings()
    sections = _sections_for_legacy_compare(_load_sections(state), state, settings)
    bundles = _load_bundles(state)

    hits_by_section: dict[str, list] = {
        sid: list(bundle.policy_hits) for sid, bundle in bundles.items()
    }
    categories_by_section = {
        sid: list(bundle.categories) for sid, bundle in bundles.items()
    }
    playbook_hints = _playbook_hints(state)
    doc_catalog = catalog_doc_categories(list(state.get("indexed_policies") or []))

    substantive_sections = [
        s for s in sections if _bundle_is_substantive(bundles.get(s.section_id), settings=settings)
    ]

    coverage_warnings: list[str] = []
    ipc_items: list = []
    if settings.policy_coverage_enabled:
        hits_by_section, ipc_items, coverage_warnings = apply_coverage_gate(
            substantive_sections,
            hits_by_section,
            categories_by_section,
            settings=settings,
            doc_catalog_categories=doc_catalog or None,
        )

    sections_with_policy = [
        s
        for s in substantive_sections
        if hits_by_section.get(s.section_id)
    ]

    related_by_section = {}
    raw_context = state.get("section_context_by_id") or {}
    if settings.section_cross_ref_enabled and raw_context:
        from review_agent.services.section_cross_reference import RelatedSectionBundle

        for sid, payload in raw_context.items():
            if not isinstance(payload, dict):
                continue
            related = payload.get("related") or []
            tuples = [
                (r.get("section_id", ""), r.get("title", ""), r.get("excerpt", ""))
                for r in related
                if isinstance(r, dict)
            ]
            related_by_section[sid] = RelatedSectionBundle(
                primary_section_id=str(payload.get("primary_section_id") or sid),
                related=[t for t in tuples if t[0]],
                resolution_reason=str(payload.get("resolution_reason") or ""),
            )

    if settings.section_cross_ref_enabled:
        from review_agent.services.section_cross_reference import (
            merge_category_siblings_into_bundle,
            resolve_category_siblings,
        )

        for section in sections_with_policy:
            siblings = resolve_category_siblings(
                section,
                sections,
                categories_by_section,
            )
            if not siblings:
                continue
            related_by_section[section.section_id] = merge_category_siblings_into_bundle(
                related_by_section.get(section.section_id),
                siblings,
                primary_section_id=section.section_id,
            )

    items, compare_warnings, batch_stats = await compare_all_sections(
        sections_with_policy,
        hits_by_section,
        contract_type=state.get("contract_type"),
        memory_context=state.get("memory_context") or "",
        settings=settings,
        playbook_hints_by_document=playbook_hints,
        categories_by_section=categories_by_section,
        related_by_section=related_by_section,
        doc_catalog_categories=doc_catalog or None,
    )
    incorporation_upgraded = 0
    if settings.incorporation_guard_enabled:
        from review_agent.services.incorporation_guard import apply_incorporation_guard

        sections_by_id = {s.section_id: s for s in sections}
        items, incorporation_upgraded = apply_incorporation_guard(items, sections_by_id)
    topic_mismatch_downgraded = 0
    if settings.topic_mismatch_guard_enabled:
        from review_agent.services.topic_mismatch_guard import apply_topic_mismatch_guard

        sections_by_id = {s.section_id: s for s in sections}
        items, topic_mismatch_downgraded = apply_topic_mismatch_guard(
            items,
            sections_by_id=sections_by_id,
            categories_by_section=categories_by_section,
            hits_by_section=hits_by_section,
        )
    if ipc_items:
        items = list(ipc_items) + list(items)
    compare_warnings = coverage_warnings + compare_warnings
    if incorporation_upgraded:
        compare_warnings.append(
            f"incorporation guard upgraded {incorporation_upgraded} finding(s)"
        )
    if topic_mismatch_downgraded:
        compare_warnings.append(
            f"topic mismatch guard downgraded {topic_mismatch_downgraded} finding(s)"
        )

    path_counts = {"dense": 0, "fts": 0, "metadata": 0}
    for bundle in bundles.values():
        meta = bundle.retrieval_meta or {}
        if meta.get("dense_count", 0):
            path_counts["dense"] += 1
        if meta.get("fts_count", 0):
            path_counts["fts"] += 1
        if meta.get("metadata_count", 0):
            path_counts["metadata"] += 1

    stats = {
        **dict(state.get("compliance_stats") or {}),
        "compliance_mode": "section_first",
        "sections_total": len(sections),
        "sections_with_policy": len(sections_with_policy),
        "sections_no_policy": len(sections) - len(sections_with_policy),
        "compare_items": len(items),
        "coverage_gate_ipc_count": len(ipc_items),
        "incorporation_guard_upgraded": incorporation_upgraded,
        "topic_mismatch_guard_downgraded": topic_mismatch_downgraded,
        "retrieval_paths_used": path_counts,
        **batch_stats,
    }
    return {
        "section_compare_items": [i.model_dump(mode="json") for i in items],
        "compliance_stats": stats,
        "warnings": compare_warnings,
        "failed_sections": compare_failed_entries(items),
    }


async def merge_section_findings_node(
    state: ReviewState,
    client: DocumentMCPClient,
) -> dict[str, Any]:
    _ = client
    from document_core.schemas.compliance import ComplianceFinding
    from review_agent.schemas.section_compare import SectionCompareItem

    bundles = _load_bundles(state)
    raw_items = state.get("section_compare_items") or []
    items = [SectionCompareItem.model_validate(i) for i in raw_items]
    merged = merge_section_findings(
        items,
        bundles,
        hints_by_document=_playbook_hints(state),
        sections_by_id={s.section_id: s for s in _load_sections(state)},
    )
    obligation_findings = [
        ComplianceFinding.model_validate(item)
        for item in (state.get("obligation_findings") or [])
    ]
    all_findings = obligation_findings + merged.findings
    return {
        "findings": all_findings,
        "warnings": merged.warnings,
        "gap_section_ids": merged.gap_section_ids,
        "no_policy_gap_ids": merged.no_policy_gap_ids,
        "compare_omitted_gap_ids": merged.compare_omitted_gap_ids,
        "unclear_finding_ids": merged.unclear_finding_ids,
        "unclear_recompare_finding_ids": merged.unclear_recompare_finding_ids,
        "conflict_pairs": [list(pair) for pair in merged.conflict_pairs],
    }


async def final_gap_verify_node(
    state: ReviewState,
    client: DocumentMCPClient,
) -> dict[str, Any]:
    settings = get_settings()
    sections = _load_sections(state)
    sections_by_id = {s.section_id: s for s in sections}
    bundles = _load_bundles(state)

    gap_ids = list(state.get("gap_section_ids") or [])
    no_policy_ids = list(state.get("no_policy_gap_ids") or [])
    compare_omitted_ids = list(state.get("compare_omitted_gap_ids") or [])
    unclear_ids = list(state.get("unclear_finding_ids") or [])
    recompare_ids = list(state.get("unclear_recompare_finding_ids") or [])
    raw_pairs = state.get("conflict_pairs") or []
    conflict_pairs = [tuple(p) for p in raw_pairs if len(p) == 2]
    existing = list(state.get("findings") or [])

    new_findings, warnings, stats, superseded_ids = await run_final_gap_verify(
        client=client,
        tenant_id=state["tenant_id"],
        sections_by_id=sections_by_id,
        bundles=bundles,
        gap_section_ids=gap_ids,
        no_policy_gap_ids=no_policy_ids,
        compare_omitted_gap_ids=compare_omitted_ids,
        unclear_finding_ids=unclear_ids,
        unclear_recompare_finding_ids=recompare_ids,
        conflict_pairs=conflict_pairs,
        existing_findings=existing,
        contract_type=state.get("contract_type"),
        policy_type=state.get("policy_type"),
        memory_context=state.get("memory_context") or "",
        settings=settings,
    )

    superseded_set = set(superseded_ids)
    resolved_section_ids = {f.contract_section_id for f in new_findings if f.contract_section_id}
    _gap_types = frozenset({"no_policy", "compare_omitted", "coverage_backfill"})
    kept_findings = [
        f
        for f in existing
        if f.finding_id not in superseded_set
        and not (
            f.contract_section_id in resolved_section_ids
            and f.metadata.get("gap_type") in _gap_types
        )
    ]
    merged_findings = kept_findings + new_findings

    coverage_warnings: list[str] = []
    section_coverage_meta: dict[str, Any] = {}
    if settings.enforce_section_coverage:
        reviewable = sections or reviewable_sections(
            [IndexedChunk.model_validate(s) for s in (state.get("contract_sections") or [])],
            min_chars=settings.review_min_section_chars,
        )
        coverage = ensure_section_coverage(
            reviewable,
            merged_findings,
            min_chars=settings.review_min_section_chars,
            sections_by_id=sections_by_id,
            settings=settings,
        )
        merged_findings = coverage.findings
        coverage_warnings = coverage.warnings
        section_coverage_meta = {
            "reviewable_count": coverage.reviewable_count,
            "uncovered_before": coverage.uncovered_before,
            "backfill_count": coverage.backfill_count,
        }

    updated_bundles = {k: v.model_dump(mode="json") for k, v in bundles.items()}

    return {
        "findings": merged_findings,
        "section_retrieval_by_id": updated_bundles,
        "final_verify_stats": stats,
        "section_coverage": section_coverage_meta,
        "superseded_finding_ids": list(dict.fromkeys(superseded_ids)),
        "warnings": warnings + coverage_warnings,
        "compliance_stats": {
            **dict(state.get("compliance_stats") or {}),
            "final_gap_verify": stats,
            "section_coverage": section_coverage_meta,
        },
    }
