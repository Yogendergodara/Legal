"""Build ReviewArtifact from LangGraph state — pure read model, no I/O."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from document_core.schemas.chunk import IndexedChunk
from document_core.schemas.compliance import ComplianceFinding, ComplianceStatus
from review_agent.config import ReviewSettings, get_settings
from review_agent.schemas.review_artifact import (
    ARTIFACT_VERSION,
    GapLlmAuditRow,
    ObligationRoutingAuditRow,
    RetrievalAuditRow,
    RetrievalHitRef,
    ReviewArtifact,
    ReviewArtifactOps,
    SectionAuditRow,
)
from review_agent.schemas.section_compare import SectionCompareItem
from review_agent.schemas.section_retrieval import SectionRetrievalBundle
from review_agent.state.review_state import ReviewState


def _int_val(data: dict[str, Any], key: str, default: int = 0) -> int:
    raw = data.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _slim_retrieval_row(
    bundle: SectionRetrievalBundle,
    *,
    include_hit_refs: bool,
    max_hit_refs: int,
) -> RetrievalAuditRow:
    hits: list[RetrievalHitRef] = []
    if include_hit_refs:
        for hit in bundle.policy_hits[:max_hit_refs]:
            parent = hit.parent_chunk
            hits.append(
                RetrievalHitRef(
                    document_id=str(parent.document_id),
                    section_id=parent.section_id,
                    score=float(hit.score),
                )
            )
    return RetrievalAuditRow(
        section_id=bundle.section_id,
        categories=list(bundle.categories),
        hit_count=len(bundle.policy_hits),
        hits=hits,
        retrieval_meta=dict(bundle.retrieval_meta or {}),
    )


def _build_sections(
    state: ReviewState,
    retrieval_by_id: dict[str, SectionRetrievalBundle],
) -> list[SectionAuditRow]:
    raw = state.get("section_review_sections") or state.get("contract_sections") or []
    rows: list[SectionAuditRow] = []
    for item in raw:
        if isinstance(item, IndexedChunk):
            section = item
        else:
            section = IndexedChunk.model_validate(item)
        bundle = retrieval_by_id.get(section.section_id)
        rows.append(
            SectionAuditRow(
                section_id=section.section_id,
                title=section.title or "",
                char_count=len((section.text or "").strip()),
                categories=list(bundle.categories) if bundle else [],
            )
        )
    return rows


def _build_discovery(state: ReviewState) -> dict[str, Any]:
    indexed: list[dict[str, Any]] = []
    for entry in state.get("indexed_policies") or []:
        if not isinstance(entry, dict):
            continue
        indexed.append(
            {
                "document_id": str(entry.get("document_id") or ""),
                "policy_ref": entry.get("policy_ref"),
                "title": entry.get("title"),
            }
        )
    return {
        "discovered_policy_document_ids": list(
            state.get("discovered_policy_document_ids") or []
        ),
        "discovery_warnings": list(state.get("discovery_warnings") or []),
        "indexed_policies": indexed,
    }


def _build_obligation_routing(state: ReviewState) -> list[ObligationRoutingAuditRow]:
    rows: list[ObligationRoutingAuditRow] = []
    for finding in state.get("obligation_findings") or []:
        if isinstance(finding, dict):
            meta = dict(finding.get("metadata") or {})
            audit = dict(meta.get("routing_audit") or {})
        else:
            meta = dict(getattr(finding, "metadata", None) or {})
            audit = dict(meta.get("routing_audit") or {})
        if not audit:
            continue
        rows.append(
            ObligationRoutingAuditRow(
                obligation_id=str(audit.get("obligation_id") or meta.get("obligation_id") or ""),
                section_id=str(audit.get("section_id") or ""),
                routing_source=str(audit.get("routing_source") or ""),
                confidence=float(audit.get("routing_confidence") or 0.0),
                candidate_doc_ids=[str(x) for x in (audit.get("candidate_doc_ids") or [])],
                candidate_titles=[str(x) for x in (audit.get("candidate_titles") or [])],
                evidence_decision=str(audit.get("evidence_decision") or ""),
                evidence_reason=str(audit.get("evidence_reason") or ""),
                queries_used=[str(x) for x in (audit.get("queries_used") or [])],
                hit_count=int(audit.get("hit_count") or 0),
            )
        )
    if rows:
        return rows
    for obligation_id, audit in (state.get("obligation_routing_by_id") or {}).items():
        if not isinstance(audit, dict):
            continue
        rows.append(
            ObligationRoutingAuditRow(
                obligation_id=str(obligation_id),
                section_id=str(audit.get("section_id") or ""),
                routing_source=str(audit.get("routing_source") or ""),
                confidence=float(audit.get("confidence") or 0.0),
            )
        )
    return rows


def _build_gap_llm(findings: list[ComplianceFinding]) -> list[GapLlmAuditRow]:
    rows: list[GapLlmAuditRow] = []
    for finding in findings:
        meta = finding.metadata or {}
        if meta.get("final_verify") != "gap_llm":
            continue
        rows.append(
            GapLlmAuditRow(
                section_id=finding.contract_section_id or "",
                finding_id=finding.finding_id,
                status=finding.status.value,
                rationale_preview=(finding.rationale or "")[:200],
            )
        )
    return rows


def _build_ops(
    *,
    compliance_stats: dict[str, Any],
    final_verify_stats: dict[str, Any],
    section_coverage: dict[str, Any],
    superseded_finding_ids: list[str],
    findings: list[ComplianceFinding],
    failed_sections: list[dict[str, Any]],
) -> ReviewArtifactOps:
    failed = list(failed_sections)
    zero_hit_ids = [
        str(entry["section_id"])
        for entry in failed
        if entry.get("error_code") == "retrieval_zero_hit"
    ]
    return ReviewArtifactOps(
        retrieval_retry_sections=_int_val(compliance_stats, "retrieval_retry_sections"),
        retrieval_max_attempts_used=_int_val(
            compliance_stats, "retrieval_max_attempts_used"
        ),
        retrieval_zero_hit_sections=_int_val(
            compliance_stats, "retrieval_zero_hit_sections"
        ),
        llm_batches_failed=_int_val(compliance_stats, "llm_batches_failed"),
        gap_llm_sections=_int_val(final_verify_stats, "gap_llm_sections"),
        gap_llm_failed=_int_val(final_verify_stats, "gap_llm_failed"),
        unclear_recompared=_int_val(final_verify_stats, "unclear_recompared"),
        conflicts_recompared=_int_val(final_verify_stats, "conflicts_recompared"),
        conflicts_unresolved=_int_val(final_verify_stats, "conflicts_unresolved"),
        superseded_count=len(superseded_finding_ids),
        ungrounded_count=sum(1 for f in findings if f.grounded is False),
        grounding_downgraded_count=sum(
            1 for f in findings if (f.metadata or {}).get("grounding_failed") is True
        ),
        backfill_count=_int_val(section_coverage, "backfill_count"),
        post_grounding_backfill_count=_int_val(
            section_coverage, "post_grounding_backfill_count"
        ),
        playbook_compare_count=sum(
            1 for f in findings if (f.metadata or {}).get("source") == "playbook_compare"
        ),
        policy_conflict_count=sum(
            1 for f in findings if f.status == ComplianceStatus.POLICY_CONFLICT
        ),
        guard_checked=_int_val(compliance_stats, "guard_checked"),
        guard_failed=_int_val(compliance_stats, "guard_failed"),
        quote_repair_attempts=_int_val(compliance_stats, "quote_repair_attempts"),
        quote_repair_success=_int_val(compliance_stats, "quote_repair_success"),
        guard_inference_ok=_int_val(compliance_stats, "guard_inference_ok"),
        guard_repair_attempts=_int_val(compliance_stats, "guard_repair_attempts"),
        guard_repair_success=_int_val(compliance_stats, "guard_repair_success"),
        reranker_cross_encoder_sections=_int_val(
            compliance_stats, "reranker_cross_encoder_sections"
        ),
        reranker_lexical_fallback_sections=_int_val(
            compliance_stats, "reranker_lexical_fallback_sections"
        ),
        degraded_section_count=len(failed),
        retrieval_zero_hit_section_ids=zero_hit_ids,
    )


def build_review_artifact(
    state: ReviewState,
    *,
    findings: list[ComplianceFinding] | None = None,
    settings: ReviewSettings | None = None,
) -> ReviewArtifact:
    """Pure function — assemble audit JSON from existing pipeline state."""
    cfg = settings or get_settings()
    final_findings = list(findings or state.get("grounded_findings") or [])
    ingest = state.get("ingest_result")
    contract_document_id = (
        str(ingest.document_id) if ingest is not None else str(state.get("contract_document_id") or "")
    )

    raw_bundles = state.get("section_retrieval_by_id") or {}
    retrieval_by_id = {
        key: SectionRetrievalBundle.model_validate(value)
        for key, value in raw_bundles.items()
    }

    compare_items = [
        SectionCompareItem.model_validate(item)
        for item in (state.get("section_compare_items") or [])
    ]

    superseded_finding_ids = list(state.get("superseded_finding_ids") or [])
    failed_sections = list(state.get("failed_sections") or [])
    compliance_stats = dict(state.get("compliance_stats") or {})
    final_verify_stats = dict(state.get("final_verify_stats") or {})
    section_coverage = dict(state.get("section_coverage") or {})

    routing = dict(state.get("contract_routing") or {})
    if state.get("contract_type") and "contract_type" not in routing:
        routing["contract_type"] = state.get("contract_type")

    retrieval_rows = [
        _slim_retrieval_row(
            bundle,
            include_hit_refs=cfg.artifact_include_hit_refs,
            max_hit_refs=cfg.artifact_max_hit_refs_per_section,
        )
        for bundle in retrieval_by_id.values()
    ]

    obligation_rows = _build_obligation_routing(state)
    pipeline = "obligation_routing" if obligation_rows else "section_first"

    return ReviewArtifact(
        artifact_version=ARTIFACT_VERSION,
        run_id=str(state.get("thread_id") or ""),
        pipeline=pipeline,
        generated_at=datetime.now(timezone.utc),
        tenant_id=str(state.get("tenant_id") or ""),
        contract_document_id=contract_document_id,
        contract_title=str(state.get("contract_title") or ""),
        sections=_build_sections(state, retrieval_by_id),
        routing=routing,
        discovery=_build_discovery(state),
        retrieval=retrieval_rows,
        compare_items=compare_items,
        work_queue={
            "gap_section_ids": list(state.get("gap_section_ids") or []),
            "unclear_finding_ids": list(state.get("unclear_finding_ids") or []),
            "unclear_recompare_finding_ids": list(state.get("unclear_recompare_finding_ids") or []),
            "conflict_pairs": list(state.get("conflict_pairs") or []),
        },
        gap_llm=_build_gap_llm(final_findings),
        obligation_routing=obligation_rows,
        superseded_finding_ids=superseded_finding_ids,
        final_verify_stats=final_verify_stats,
        section_coverage=section_coverage,
        compliance_stats=compliance_stats,
        degraded_sections=failed_sections,
        ops=_build_ops(
            compliance_stats=compliance_stats,
            final_verify_stats=final_verify_stats,
            section_coverage=section_coverage,
            superseded_finding_ids=superseded_finding_ids,
            findings=final_findings,
            failed_sections=failed_sections,
        ),
    )
