"""Phase 10 section-first compare, merge, and gap verify nodes."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from document_core.schemas.chunk import IndexedChunk
from document_core.schemas.compliance import ComplianceFinding, ComplianceStatus, Severity
from review_agent.clients.document_client import DocumentMCPClient
from review_agent.config import get_settings
from review_agent.errors import FatalPipelineError
from review_agent.schemas.evidence_sufficiency import EvidenceSufficiencyResult
from review_agent.schemas.obligation import ContractObligation
from review_agent.schemas.obligation_retrieval import ObligationRetrievalBundle
from review_agent.schemas.section_compare import SectionCompareItem
from review_agent.schemas.section_retrieval import SectionRetrievalBundle
from review_agent.services.final_verify_llm import run_final_gap_verify
from review_agent.services.policy_coverage import apply_coverage_gate, catalog_doc_categories
from review_agent.services.section_compare_llm import compare_all_sections
from review_agent.services.playbook_context import build_playbook_hints_by_document
from review_agent.services.section_coverage import ensure_section_coverage, reviewable_sections
from review_agent.resilience.failed_sections import compare_failed_entries
from review_agent.services.recovery_gap_candidates import promote_recovery_compare_omitted_gaps
from review_agent.services.routing_scope import review_catalog_doc_ids
from review_agent.services.section_merge import merge_section_findings
from review_agent.services.routing_tenant import obligation_routing_active
from review_agent.services.retrieval_relevance import is_incompatible_hit
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
    hints = build_playbook_hints_by_document(state.get("indexed_policies"))
    scope = review_catalog_doc_ids(state)
    if scope:
        hints = {doc_id: value for doc_id, value in hints.items() if doc_id in scope}
    return hints


_IPC_FALLBACK_STATUSES = frozenset({
    ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
    ComplianceStatus.INCONCLUSIVE,
})


def _ipc_fallback_section_ids(state: ReviewState) -> set[str]:
    """Sections where every obligation finding is IPC/inconclusive — eligible for section-path recovery."""
    by_section: dict[str, list[ComplianceStatus]] = defaultdict(list)
    for raw in state.get("obligation_findings") or []:
        finding = (
            raw
            if isinstance(raw, ComplianceFinding)
            else ComplianceFinding.model_validate(raw)
        )
        if finding.contract_section_id:
            by_section[finding.contract_section_id].append(finding.status)
    return {
        sid
        for sid, statuses in by_section.items()
        if statuses
        and all(status in _IPC_FALLBACK_STATUSES for status in statuses)
        and not any(
            status in (ComplianceStatus.NON_COMPLIANT, ComplianceStatus.COMPLIANT)
            for status in statuses
        )
    }


def _ipc_fallback_section_ids_from_evidence(state: ReviewState) -> set[str]:
    """PF-1C: derive ipc_fallback sections from evidence (pre–obligation_compare LLM)."""
    evidence_raw = state.get("obligation_evidence_by_id") or {}
    retrieval_raw = state.get("obligation_retrieval_by_id") or {}
    obligations = [
        ContractObligation.model_validate(item) for item in (state.get("obligations") or [])
    ]
    if not obligations:
        return set()

    by_section: dict[str, list[str]] = defaultdict(list)
    for ob in obligations:
        oid = ob.obligation_id
        sid = ob.section_id
        if oid in evidence_raw:
            decision = EvidenceSufficiencyResult.model_validate(evidence_raw[oid]).decision
            by_section[sid].append(decision)
            continue
        if oid in retrieval_raw:
            bundle = ObligationRetrievalBundle.model_validate(retrieval_raw[oid])
            if bundle.skipped_reason:
                by_section[sid].append("ipc")
            else:
                by_section[sid].append("missing")
            continue
        by_section[sid].append("missing")

    ipc_only: set[str] = set()
    for sid, decisions in by_section.items():
        if not decisions or "compare" in decisions or "missing" in decisions:
            continue
        if all(d == "ipc" for d in decisions):
            ipc_only.add(sid)
    return ipc_only


def _ipc_fallback_section_ids_for_cutover(state: ReviewState, settings) -> set[str]:
    """PG-2: prefer evidence-based ipc_fallback when evidence gate has run."""
    _ = settings
    if state.get("obligation_evidence_by_id"):
        return _ipc_fallback_section_ids_from_evidence(state)
    return _ipc_fallback_section_ids(state)


def _sections_for_legacy_compare(
    sections: list[IndexedChunk],
    state: ReviewState,
    settings,
) -> list[IndexedChunk]:
    tenant_id = str(state.get("tenant_id") or "")
    if not obligation_routing_active(tenant_id, settings) or not settings.obligation_compare_enabled:
        return sections
    mode = settings.obligation_section_cutover_mode
    if mode == "legacy_parallel":
        return sections
    covered = {
        ContractObligation.model_validate(item).section_id
        for item in (state.get("obligations") or [])
    }
    if not covered:
        return sections
    if mode == "ipc_fallback":
        ipc_fallback = _ipc_fallback_section_ids_for_cutover(state, settings)
        return [
            section
            for section in sections
            if section.section_id not in covered or section.section_id in ipc_fallback
        ]
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


def _emit_incompatible_family_ipc(
    sections: list[IndexedChunk],
    hits_by_section: dict[str, list],
    categories_by_section: dict[str, list[str]],
    *,
    doc_catalog_categories: dict[str, list[str]] | None,
) -> list[SectionCompareItem]:
    """Pre-compare IPC when every retrieved hit is an incompatible policy family."""
    items: list[SectionCompareItem] = []
    for section in sections:
        sid = section.section_id
        hits = list(hits_by_section.get(sid) or [])
        if not hits:
            continue
        section_title = section.title or sid
        section_categories = categories_by_section.get(sid, [])
        if not all(
            is_incompatible_hit(
                section_categories,
                section_title,
                hit,
                doc_catalog_categories=doc_catalog_categories,
            )
            for hit in hits
        ):
            continue
        hits_by_section[sid] = []
        items.append(
            SectionCompareItem(
                section_id=sid,
                dimension_label=section.title or sid,
                status=ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
                severity=Severity.INFO,
                contract_quote="",
                policy_quote="",
                rationale=(
                    "Retrieved policies were off-topic for this contract section "
                    "(reason=incompatible_policy_family). Compare skipped to avoid false gaps."
                ),
                confidence=0.9,
            )
        )
    return items


async def section_compare_llm_node(
    state: ReviewState,
    client: DocumentMCPClient,
) -> dict[str, Any]:
    settings = get_settings()
    try:
        return await _section_compare_llm_impl(state, client, settings)
    except FatalPipelineError:
        raise
    except Exception as exc:  # noqa: BLE001
        if not settings.compare_branch_fail_open:
            raise
        prior = dict(state.get("compliance_stats") or {})
        return {
            "section_compare_items": [],
            "compliance_stats": {
                **prior,
                "section_compare_failed": True,
                "section_compare_fail_reason": str(exc)[:500],
            },
            "warnings": [f"section compare branch failed (fail-open): {exc}"],
        }


async def _section_compare_llm_impl(
    state: ReviewState,
    client: DocumentMCPClient,
    settings,
) -> dict[str, Any]:
    _ = client
    all_sections = _load_sections(state)
    sections = _sections_for_legacy_compare(all_sections, state, settings)
    obligation_skipped = len(all_sections) - len(sections)
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

    retrieval_gate_by_section = {
        sid: bool((bundles.get(sid) and (bundles[sid].retrieval_meta or {}).get("relevance_gate_applied")))
        for sid in hits_by_section
    }

    coverage_warnings: list[str] = []
    ipc_items: list = []
    if settings.policy_coverage_enabled:
        hits_by_section, ipc_items, coverage_warnings = apply_coverage_gate(
            substantive_sections,
            hits_by_section,
            categories_by_section,
            settings=settings,
            doc_catalog_categories=doc_catalog or None,
            retrieval_gate_applied_by_section=retrieval_gate_by_section,
        )

    incompatible_ipc = _emit_incompatible_family_ipc(
        substantive_sections,
        hits_by_section,
        categories_by_section,
        doc_catalog_categories=doc_catalog or None,
    )
    if incompatible_ipc:
        ipc_items = list(ipc_items) + incompatible_ipc
        coverage_warnings.append(
            f"pre-compare incompatible-family IPC for {len(incompatible_ipc)} section(s)"
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
        retrieval_gate_applied_by_section=retrieval_gate_by_section,
        allowed_document_ids=review_catalog_doc_ids(state),
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
            doc_catalog_categories=doc_catalog or None,
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
        "section_compare_pool_total": len(sections),
        "section_compare_obligation_skipped": obligation_skipped,
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
    settings = get_settings()
    compare_omitted_ids, gap_ids, promoted = promote_recovery_compare_omitted_gaps(
        compare_items=items,
        bundles=bundles,
        obligation_findings=obligation_findings,
        section_findings=merged.findings,
        compare_omitted_gap_ids=merged.compare_omitted_gap_ids,
        gap_section_ids=merged.gap_section_ids,
        enabled=settings.recovery_promote_obligation_ipc_gaps,
    )
    warnings = list(merged.warnings)
    if promoted:
        warnings.append(
            f"{len(promoted)} section(s) promoted to compare_omitted for final verify recovery."
        )
    all_findings = obligation_findings + merged.findings
    compliance_stats = dict(state.get("compliance_stats") or {})
    compliance_stats["recovery_compare_omitted_promoted"] = len(promoted)
    compliance_stats["recovery_compare_omitted_eligible"] = len(compare_omitted_ids)
    return {
        "findings": all_findings,
        "warnings": warnings,
        "gap_section_ids": gap_ids,
        "no_policy_gap_ids": merged.no_policy_gap_ids,
        "compare_omitted_gap_ids": compare_omitted_ids,
        "unclear_finding_ids": merged.unclear_finding_ids,
        "unclear_recompare_finding_ids": merged.unclear_recompare_finding_ids,
        "conflict_pairs": [list(pair) for pair in merged.conflict_pairs],
        "compliance_stats": compliance_stats,
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
