"""LangGraph nodes for section-first contract compliance review."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from document_core.schemas.chunk import (
    DocumentKind,
    IndexedChunk,
    IngestRequest,
    IngestResult,
    ListSectionsRequest,
    StructureConfidence,
)
from document_core.schemas.registry import RegisterContractRequest
from document_core.services.registry import stable_contract_document_id
from document_core.schemas.compliance import ComplianceStatus, ReviewReport
from review_agent.clients.document_client import DocumentMCPClient
from review_agent.config import get_settings
from review_agent.models.llm_gateway import get_llm_limiter_stats
from review_agent.reports.generator import render_markdown_report
from review_agent.reports.summary_llm import maybe_llm_summary_paragraph
from review_agent.services.finding_enrich import (
    build_policy_title_map,
    enrich_findings_policy_titles,
)
from review_agent.services.guard_pass import run_guard_pass
from review_agent.services.grounding_quote import verify_quote_with_repair
from review_agent.services.review_artifact import build_review_artifact
from review_agent.services.section_coverage import ensure_section_coverage, reviewable_sections
from review_agent.services.section_gap_status import gap_status_summary
from review_agent.state.review_state import ReviewState


async def _ingest_contract_from_text(
    state: ReviewState,
    client: DocumentMCPClient,
    contract_text: str,
) -> UUID:
    tenant_id = state["tenant_id"]
    thread_id = str(state.get("thread_id") or "inline")
    contract_ref = f"query-review-{thread_id}"
    document_id = stable_contract_document_id(tenant_id, contract_ref)
    title = str(state.get("contract_title") or "Contract").strip() or "Contract"
    contract_type = state.get("contract_type")

    await client.register_contract(
        RegisterContractRequest(
            tenant_id=tenant_id,
            contract_ref=contract_ref,
            title=title,
            document_id=document_id,
            contract_type=contract_type,
        )
    )
    await client.ingest_document(
        IngestRequest(
            tenant_id=tenant_id,
            document_id=document_id,
            title=title,
            kind=DocumentKind.CONTRACT,
            text=contract_text,
            metadata={
                "contract_ref": contract_ref,
                "contract_type": contract_type,
                "source": "inline_contract_text",
            },
        )
    )
    return document_id


async def contract_parser_node(state: ReviewState, client: DocumentMCPClient) -> dict[str, Any]:
    doc_id_raw = state.get("contract_document_id")
    contract_text = str(state.get("contract_text") or "").strip()
    load_warnings: list[str] = []

    if not doc_id_raw and contract_text:
        document_id = await _ingest_contract_from_text(state, client, contract_text)
        doc_id_raw = str(document_id)
        load_warnings.append("ingested contract from contract_text before review")

    if not doc_id_raw:
        raise ValueError("contract_document_id or contract_text is required")

    document_id = UUID(str(doc_id_raw))
    sections = await client.list_sections(
        ListSectionsRequest(
            tenant_id=state["tenant_id"],
            document_id=document_id,
            kind=DocumentKind.CONTRACT,
        )
    )
    if not sections:
        raise ValueError(f"contract document not indexed: {document_id}")

    title = (
        state.get("contract_title")
        or str(sections[0].metadata.get("document_title") or "").strip()
        or "Contract"
    )
    ingest_result = IngestResult(
        document_id=document_id,
        tenant_id=state["tenant_id"],
        kind=DocumentKind.CONTRACT,
        title=title,
        parent_count=len(sections),
        child_count=0,
        structure_confidence=StructureConfidence.HIGH,
        warnings=(
            ["loaded existing contract by document_id"]
            if not load_warnings
            else load_warnings
        ),
    )
    return {
        "contract_document_id": str(document_id),
        "ingest_result": ingest_result,
        "contract_sections": sections,
        "warnings": list(ingest_result.warnings),
    }


async def index_policies_node(state: ReviewState, client: DocumentMCPClient) -> dict[str, Any]:
    warnings: list[str] = []
    indexed_policies: list[dict[str, Any]] = list(state.get("indexed_policies") or [])
    indexed_ids = {str(entry.get("document_id")) for entry in indexed_policies if entry.get("document_id")}

    for entry in state.get("discovered_policies") or []:
        doc_id = str(entry.get("document_id") or "")
        if not doc_id or doc_id in indexed_ids:
            continue
        try:
            sections = await client.list_sections(
                ListSectionsRequest(
                    tenant_id=state["tenant_id"],
                    document_id=UUID(doc_id),
                    kind=DocumentKind.POLICY,
                )
            )
        except (ValueError, TypeError):
            warnings.append(f"scoped policy {doc_id!r} has invalid document_id")
            continue
        if not sections:
            warnings.append(f"scoped policy {doc_id!r} not found in document store")
            continue
        indexed_policies.append(
            {
                "document_id": doc_id,
                "title": entry.get("title") or sections[0].metadata.get("document_title") or sections[0].title or "Policy",
                "policy_type": entry.get("policy_type"),
            }
        )
        indexed_ids.add(doc_id)

    return {
        "warnings": warnings,
        "indexed_policies": indexed_policies,
    }


async def clause_detection_node(state: ReviewState, client: DocumentMCPClient) -> dict[str, Any]:
    existing = state.get("contract_sections")
    if existing:
        return {"contract_sections": existing}

    ingest = state["ingest_result"]
    sections = await client.list_sections(
        ListSectionsRequest(
            tenant_id=state["tenant_id"],
            document_id=ingest.document_id,
            kind=DocumentKind.CONTRACT,
        )
    )
    return {"contract_sections": sections}


async def grounding_node(state: ReviewState, client: DocumentMCPClient) -> dict[str, Any]:
    settings = get_settings()
    ingest = state["ingest_result"]
    grounded: list = []
    warnings: list[str] = []
    grounding_stats: dict[str, int] = {
        "quote_repair_attempts": 0,
        "quote_repair_success": 0,
    }
    title_map = build_policy_title_map(
        state.get("indexed_policies") or [],
        state.get("discovered_policies"),
    )
    findings = enrich_findings_policy_titles(state.get("findings") or [], title_map)

    for finding in findings:
        if finding.status.value == "INSUFFICIENT_POLICY_CONTEXT":
            grounded.append(finding.model_copy(update={"grounded": True}))
            continue

        if not finding.contract_quote and not finding.policy_quote:
            continue

        quote_meta: dict[str, Any] = {}
        contract_quote = finding.contract_quote
        policy_quote = finding.policy_quote
        contract_ok = True
        policy_ok = True

        if finding.contract_quote:
            contract_quote, contract_ok, contract_meta = await verify_quote_with_repair(
                client,
                tenant_id=state["tenant_id"],
                document_id=ingest.document_id,
                quote=finding.contract_quote,
                section_id=finding.contract_section_id,
                settings=settings,
                stats=grounding_stats,
                verify_fn=client.verify_quote,
            )
            quote_meta.update(contract_meta)

        if finding.policy_quote and finding.policy_document_id:
            policy_quote, policy_ok, policy_meta = await verify_quote_with_repair(
                client,
                tenant_id=state["tenant_id"],
                document_id=finding.policy_document_id,
                quote=finding.policy_quote,
                section_id=finding.policy_section_id or "",
                settings=settings,
                stats=grounding_stats,
                verify_fn=client.verify_policy_quote,
            )
            quote_meta.update(policy_meta)

        ok = contract_ok and policy_ok
        if (
            not ok
            and settings.grounding_relax_compliant_empty_policy
            and finding.status == ComplianceStatus.COMPLIANT
            and contract_ok
            and not (finding.policy_quote or "").strip()
        ):
            from review_agent.services.quote_validate import allows_empty_policy_quote

            if allows_empty_policy_quote(
                finding.status,
                finding.rationale,
                contract_ok=contract_ok,
            ):
                ok = True
                policy_ok = True
        if ok:
            meta = dict(finding.metadata or {})
            meta.update(quote_meta)
            grounded.append(
                finding.model_copy(
                    update={
                        "grounded": True,
                        "contract_quote": contract_quote,
                        "policy_quote": policy_quote,
                        "metadata": meta,
                    }
                )
            )
        elif settings.grounding_downgrade_mode == "keep_status_flag":
            meta = dict(finding.metadata or {})
            meta.update(quote_meta)
            meta["grounding_failed"] = True
            grounded.append(
                finding.model_copy(
                    update={
                        "grounded": False,
                        "contract_quote": contract_quote if contract_ok else "",
                        "policy_quote": policy_quote if policy_ok else "",
                        "metadata": meta,
                    }
                )
            )
            warnings.append(
                f"finding ungrounded (status kept): {finding.dimension_label}"
            )
        elif settings.grounding_downgrade_not_drop:
            meta = dict(finding.metadata or {})
            meta.update(quote_meta)
            meta["grounding_failed"] = True
            meta["prior_status"] = finding.status.value
            grounded.append(
                finding.model_copy(
                    update={
                        "status": ComplianceStatus.INCONCLUSIVE,
                        "grounded": False,
                        "metadata": meta,
                        "contract_quote": contract_quote if contract_ok else "",
                        "policy_quote": policy_quote if policy_ok else "",
                    }
                )
            )
            warnings.append(
                f"finding downgraded to INCONCLUSIVE (grounding failed): {finding.dimension_label}"
            )
        else:
            warnings.append(
                f"finding dropped (grounding failed): {finding.dimension_label}"
            )

    guard_stats: dict[str, int] = {}
    if settings.guard_pass_enabled:
        grounded, guard_warnings, guard_stats = await run_guard_pass(
            grounded,
            settings=settings,
        )
        warnings.extend(guard_warnings)

    section_coverage_meta = dict(state.get("section_coverage") or {})
    if settings.grounding_rerun_coverage and settings.enforce_section_coverage:
        raw_sections = state.get("section_review_sections") or state.get("contract_sections") or []
        reviewable = reviewable_sections(
            [IndexedChunk.model_validate(s) for s in raw_sections],
            min_chars=settings.review_min_section_chars,
        )
        coverage = ensure_section_coverage(
            reviewable,
            grounded,
            min_chars=settings.review_min_section_chars,
        )
        grounded = coverage.findings
        section_coverage_meta = {
            **section_coverage_meta,
            "post_grounding_reviewable_count": coverage.reviewable_count,
            "post_grounding_uncovered_before": coverage.uncovered_before,
            "post_grounding_backfill_count": coverage.backfill_count,
        }
        warnings.extend(coverage.warnings)

    return {
        "grounded_findings": grounded,
        "warnings": warnings,
        "section_coverage": section_coverage_meta,
        "compliance_stats": {
            **dict(state.get("compliance_stats") or {}),
            **grounding_stats,
            **guard_stats,
        },
    }


async def report_node(state: ReviewState, client: DocumentMCPClient) -> dict[str, Any]:
    _ = client
    settings = get_settings()
    ingest = state["ingest_result"]
    findings = state.get("grounded_findings") or []
    stats = dict(state.get("compliance_stats") or {})
    stats["llm_rate_limit_events"] = get_llm_limiter_stats()["rate_limit_events"]
    stats["policy_conflict_count"] = sum(
        1 for f in findings if f.status == ComplianceStatus.POLICY_CONFLICT
    )
    coverage_meta = dict(state.get("section_coverage") or {})
    finding_section_ids = sorted(
        {f.contract_section_id for f in findings if f.contract_section_id}
    )
    artifact = build_review_artifact(state, findings=findings, settings=settings)
    gap_summary = gap_status_summary(findings)
    gap_summary["compare_omitted_recovered"] = int(
        (state.get("final_verify_stats") or {}).get("compare_omitted_recovered") or 0
    )
    from review_agent.services.review_confidence import compute_review_confidence_metrics

    reviewable_count = int(coverage_meta.get("reviewable_count") or 0)
    stats["review_confidence"] = compute_review_confidence_metrics(
        findings,
        sections_total=reviewable_count or None,
    )
    report = ReviewReport(
        tenant_id=state["tenant_id"],
        contract_document_id=ingest.document_id,
        contract_title=state.get("contract_title") or ingest.title,
        findings=findings,
        warnings=list(state.get("warnings") or []),
        structure_confidence=ingest.structure_confidence.value,
        metadata={
            "thread_id": state.get("thread_id"),
            "memory_hits": len(state.get("memory_hits") or []),
            "review_policy_source": "request",
            "contract_document_id": str(ingest.document_id),
            "compliance_stats": stats,
            "section_retrieval_count": len(state.get("section_retrieval_by_id") or {}),
            "section_compare_count": len(state.get("section_compare_items") or []),
            "gap_section_count": len(state.get("gap_section_ids") or []),
            "unclear_finding_count": len(state.get("unclear_finding_ids") or []),
            "conflict_pair_count": len(state.get("conflict_pairs") or []),
            "final_verify_stats": dict(state.get("final_verify_stats") or {}),
            "section_coverage": coverage_meta,
            "reviewable_section_count": coverage_meta.get("reviewable_count", 0),
            "finding_section_ids": finding_section_ids,
            "discovered_policy_document_ids": list(
                state.get("discovered_policy_document_ids") or []
            ),
            "routing_topics": list((state.get("contract_routing") or {}).get("topics") or []),
            "discovery_warnings": list(state.get("discovery_warnings") or []),
            "pipeline": "section_first",
            "gap_status_summary": gap_summary,
            "artifact": artifact.model_dump(mode="json"),
        },
    )
    llm_paragraph, llm_warning = await maybe_llm_summary_paragraph(
        report,
        artifact=artifact,
        settings=settings,
    )
    if llm_warning:
        report.warnings.append(llm_warning)
    backfill_count = int(coverage_meta.get("backfill_count") or 0)
    if backfill_count > 0:
        report.warnings.append(f"{backfill_count} section(s) required coverage backfill")
    if state.get("memory_context"):
        report.metadata["memory_context_preview"] = state["memory_context"][:500]
    report.summary_markdown = render_markdown_report(
        report,
        artifact=artifact,
        llm_summary_paragraph=llm_paragraph,
    )
    return {"report": report}
