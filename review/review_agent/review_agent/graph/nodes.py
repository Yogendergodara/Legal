"""LangGraph nodes for contract compliance review."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from document_core.schemas.chunk import (
    DocumentKind,
    GroundingCheckRequest,
    IngestRequest,
    ListSectionsRequest,
)
from document_core.schemas.compliance import ReviewReport
from review_agent.clients.document_client import DocumentMCPClient
from review_agent.clients.policy_catalog import get_policy_catalog, index_fetched_policy
from review_agent.config import get_settings
from review_agent.dimensions.loader import load_dimensions, yaml_to_categories
from review_agent.reports.generator import render_markdown_report
from review_agent.schemas.review_category import ReviewCategory
from review_agent.services.compliance import compare_sections
from review_agent.services.compliance_llm import compare_sections_llm
from review_agent.services.finding_enrich import (
    build_policy_title_map,
    enrich_findings_policy_titles,
)
from review_agent.services.policy_plan import build_review_plan
from review_agent.services.policy_retrieval import resolve_all_policy_hits, resolve_policy_hits
from review_agent.services.alignment import build_alignment_record
from review_agent.state.review_state import ReviewState


def _parse_categories(state: ReviewState) -> list[ReviewCategory]:
    raw = state.get("review_categories") or []
    return [ReviewCategory.model_validate(item) for item in raw]


async def contract_parser_node(state: ReviewState, client: DocumentMCPClient) -> dict[str, Any]:
    request = IngestRequest(
        tenant_id=state["tenant_id"],
        title=state.get("contract_title") or "Contract",
        kind=DocumentKind.CONTRACT,
        text=state["contract_text"],
    )
    ingest_result = await client.ingest_document(request)
    warnings = list(ingest_result.warnings)
    return {
        "ingest_result": ingest_result,
        "warnings": warnings,
    }


async def index_policies_node(state: ReviewState, client: DocumentMCPClient) -> dict[str, Any]:
    settings = get_settings()
    warnings: list[str] = []
    indexed_policies: list[dict[str, Any]] = list(state.get("indexed_policies") or [])
    indexed_ids = {str(entry.get("document_id")) for entry in indexed_policies if entry.get("document_id")}
    fetched_refs: set[str] = set(state.get("fetched_policy_refs") or [])
    ref_by_doc: dict[str, str] = dict(state.get("policy_ref_by_document_id") or {})

    catalog = get_policy_catalog(
        catalog_url=settings.policy_catalog_url,
        fetch_enabled=settings.policy_fetch_enabled,
    )

    for ref in state.get("policy_refs") or []:
        if ref in fetched_refs:
            continue
        if catalog is None:
            warnings.append(f"policy_ref {ref!r} skipped: no catalog configured")
            continue
        document = await catalog.fetch_policy(state["tenant_id"], ref)
        if document is None:
            warnings.append(f"policy_ref {ref!r} not found in catalog")
            continue
        _result, entry = await index_fetched_policy(
            client,
            tenant_id=state["tenant_id"],
            document=document,
            policy_ref=ref,
            default_policy_type=state.get("policy_type"),
        )
        indexed_policies.append(entry)
        fetched_refs.add(ref)
        ref_by_doc[entry["document_id"]] = ref

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
            warnings.append(f"discovered policy {doc_id!r} has invalid document_id")
            continue
        if not sections:
            warnings.append(f"discovered policy {doc_id!r} not found in document store")
            continue
        indexed_policies.append(
            {
                "document_id": doc_id,
                "title": entry.get("title") or sections[0].metadata.get("document_title") or sections[0].title or "Policy",
                "policy_type": entry.get("policy_type"),
                "applies_to_contract_types": list(entry.get("applies_to_contract_types") or []),
            }
        )
        indexed_ids.add(doc_id)

    for idx, policy in enumerate(state.get("policy_texts") or []):
        title = policy.get("title") or f"Policy {idx + 1}"
        text = policy.get("text", "").strip()
        if not text:
            warnings.append(f"skipped empty policy: {title}")
            continue

        applies = policy.get("applies_to_contract_types") or (
            [state["contract_type"]] if state.get("contract_type") else []
        )
        result = await client.index_policy(
            IngestRequest(
                tenant_id=state["tenant_id"],
                title=title,
                kind=DocumentKind.POLICY,
                text=text,
                policy_type=policy.get("policy_type") or state.get("policy_type"),
                applies_to_contract_types=applies,
            )
        )
        indexed_policies.append(
            {
                "document_id": str(result.document_id),
                "title": title,
                "policy_type": policy.get("policy_type") or state.get("policy_type"),
                "applies_to_contract_types": list(applies),
            }
        )

    return {
        "warnings": warnings,
        "indexed_policies": indexed_policies,
        "fetched_policy_refs": sorted(fetched_refs),
        "policy_ref_by_document_id": ref_by_doc,
    }


async def clause_detection_node(state: ReviewState, client: DocumentMCPClient) -> dict[str, Any]:
    ingest = state["ingest_result"]
    sections = await client.list_sections(
        ListSectionsRequest(
            tenant_id=state["tenant_id"],
            document_id=ingest.document_id,
            kind=DocumentKind.CONTRACT,
        )
    )
    return {"contract_sections": sections}


async def policy_plan_node(state: ReviewState, client: DocumentMCPClient) -> dict[str, Any]:
    settings = get_settings()
    effective_scope = settings.review_policy_scope
    if settings.review_policy_source == "tenant_auto" and effective_scope == "request":
        effective_scope = "discovered"
    plan_settings = (
        settings.model_copy(update={"review_policy_scope": effective_scope})
        if effective_scope != settings.review_policy_scope
        else settings
    )

    policy_document_ids = (
        state.get("discovered_policy_document_ids")
        or state.get("policy_document_ids")
    )

    if settings.review_plan_mode == "static":
        categories, plan_warnings = yaml_to_categories(load_dimensions())
    else:
        categories, plan_warnings = await build_review_plan(
            client=client,
            tenant_id=state["tenant_id"],
            indexed_policies=state.get("indexed_policies") or [],
            policy_document_ids=policy_document_ids,
            contract_type=state.get("contract_type"),
            contract_sections=state.get("contract_sections") or [],
            settings=plan_settings,
        )

    serialized = [category.model_dump(mode="json") for category in categories]
    return {"review_categories": serialized, "warnings": plan_warnings}


async def policy_retrieval_node(state: ReviewState, client: DocumentMCPClient) -> dict[str, Any]:
    settings = get_settings()
    ingest = state["ingest_result"]
    catalog = get_policy_catalog(
        catalog_url=settings.policy_catalog_url,
        fetch_enabled=settings.policy_fetch_enabled,
    )

    fetched_refs: set[str] = set(state.get("fetched_policy_refs") or [])
    ref_by_doc: dict[str, str] = dict(state.get("policy_ref_by_document_id") or {})
    categories = _parse_categories(state)

    if settings.compliance_mode == "hybrid":
        policy_hits, contract_hits, retrieval_meta, retrieval_warnings = await resolve_all_policy_hits(
            client=client,
            catalog=catalog,
            tenant_id=state["tenant_id"],
            categories=categories,
            contract_document_id=ingest.document_id,
            contract_type=state.get("contract_type"),
            policy_type=state.get("policy_type"),
            fetched_refs=fetched_refs,
            policy_ref_by_doc=ref_by_doc,
            settings=settings,
        )
        alignment_by_category: dict[str, dict] = {}
        for category in categories:
            meta = retrieval_meta.get(category.category_id, {})
            record = build_alignment_record(
                category,
                policy_hits.get(category.category_id, []),
                contract_hits.get(category.category_id, []),
                meta,
                settings=settings,
            )
            alignment_by_category[category.category_id] = record.model_dump(mode="json")

        return {
            "policy_hits_by_category": policy_hits,
            "contract_hits_by_category": contract_hits,
            "retrieval_meta_by_category": retrieval_meta,
            "alignment_by_category": alignment_by_category,
            "fetched_policy_refs": sorted(fetched_refs),
            "policy_ref_by_document_id": ref_by_doc,
            "warnings": retrieval_warnings,
        }

    policy_hits: dict[str, list] = {}
    contract_hits: dict[str, list] = {}
    retrieval_meta: dict[str, dict[str, Any]] = {}

    for category in categories:
        p_hits, c_hits, meta = await resolve_policy_hits(
            client=client,
            catalog=catalog,
            tenant_id=state["tenant_id"],
            category=category,
            contract_document_id=ingest.document_id,
            contract_type=state.get("contract_type"),
            policy_type=state.get("policy_type"),
            fetched_refs=fetched_refs,
            policy_ref_by_doc=ref_by_doc,
            settings=settings,
        )
        policy_hits[category.category_id] = p_hits
        contract_hits[category.category_id] = c_hits
        retrieval_meta[category.category_id] = meta

    return {
        "policy_hits_by_category": policy_hits,
        "contract_hits_by_category": contract_hits,
        "fetched_policy_refs": sorted(fetched_refs),
        "policy_ref_by_document_id": ref_by_doc,
        "retrieval_meta_by_category": retrieval_meta,
    }


async def compliance_review_node(state: ReviewState, client: DocumentMCPClient) -> dict[str, Any]:
    _ = client
    settings = get_settings()
    findings = []
    memory_context = state.get("memory_context") or ""
    retrieval_meta = state.get("retrieval_meta_by_category") or {}
    title_map = build_policy_title_map(
        state.get("indexed_policies") or [],
        state.get("discovered_policies"),
    )

    for category in _parse_categories(state):
        contract_hits = state.get("contract_hits_by_category", {}).get(category.category_id, [])
        policy_hits = state.get("policy_hits_by_category", {}).get(category.category_id, [])
        meta = retrieval_meta.get(category.category_id, {})
        doc_id = str(category.policy_document_id) if category.policy_document_id else ""
        policy_title = title_map.get(doc_id, "")

        if settings.compliance_mode == "llm":
            finding = await compare_sections_llm(
                dimension_id=category.category_id,
                dimension_label=category.label,
                contract_hits=contract_hits,
                policy_hits=policy_hits,
                memory_context=memory_context,
                review_guidance=category.review_guidance,
                contract_type=state.get("contract_type"),
                policy_title=policy_title,
            )
        else:
            finding = compare_sections(
                dimension_id=category.category_id,
                dimension_label=category.label,
                contract_hits=contract_hits,
                policy_hits=policy_hits,
            )

        if finding:
            if meta:
                finding = finding.model_copy(
                    update={"metadata": {**finding.metadata, **meta}}
                )
            findings.append(finding)

    return {"findings": findings}


async def grounding_node(state: ReviewState, client: DocumentMCPClient) -> dict[str, Any]:
    ingest = state["ingest_result"]
    grounded: list = []
    warnings: list[str] = []
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

        ok = True
        if finding.contract_quote:
            contract_check = await client.verify_quote(
                GroundingCheckRequest(
                    tenant_id=state["tenant_id"],
                    document_id=ingest.document_id,
                    quote=finding.contract_quote,
                    section_id=finding.contract_section_id,
                )
            )
            ok = ok and contract_check.grounded

        if finding.policy_quote and finding.policy_document_id:
            policy_check = await client.verify_policy_quote(
                GroundingCheckRequest(
                    tenant_id=state["tenant_id"],
                    document_id=finding.policy_document_id,
                    quote=finding.policy_quote,
                    section_id=finding.policy_section_id,
                )
            )
            ok = ok and policy_check.grounded

        if ok:
            grounded.append(finding.model_copy(update={"grounded": True}))
        else:
            warnings.append(
                f"finding dropped (grounding failed): {finding.dimension_label}"
            )

    return {"grounded_findings": grounded, "warnings": warnings}


async def report_node(state: ReviewState, client: DocumentMCPClient) -> dict[str, Any]:
    _ = client
    ingest = state["ingest_result"]
    findings = state.get("grounded_findings") or []
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
            "review_plan_mode": get_settings().review_plan_mode,
            "review_plan_llm_filter": get_settings().review_plan_llm_filter,
            "review_policy_source": get_settings().review_policy_source,
            "category_count": len(state.get("review_categories") or []),
            "fetched_policy_refs": list(state.get("fetched_policy_refs") or []),
            "compliance_stats": dict(state.get("compliance_stats") or {}),
            "review_pipeline_mode": get_settings().review_pipeline_mode,
            "section_retrieval_count": len(state.get("section_retrieval_by_id") or {}),
            "section_compare_count": len(state.get("section_compare_items") or []),
            "discovered_policy_document_ids": list(
                state.get("discovered_policy_document_ids") or []
            ),
            "routing_topics": list((state.get("contract_routing") or {}).get("topics") or []),
            "discovery_warnings": list(state.get("discovery_warnings") or []),
        },
    )
    if state.get("memory_context"):
        report.metadata["memory_context_preview"] = state["memory_context"][:500]
    report.summary_markdown = render_markdown_report(report)
    return {"report": report}
