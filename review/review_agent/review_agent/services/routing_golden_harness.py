"""Deterministic routing pipeline harness for golden tests (Phase R8)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk, RetrievalHit
from document_core.schemas.compliance import ComplianceStatus
from document_core.schemas.policy_catalog import CatalogSearchHit
from review_agent.clients.document_client import DocumentMCPClient
from review_agent.config import ReviewSettings
from review_agent.graph.routing_nodes import _plan_from_alias, _skipped_plan
from review_agent.schemas.evidence_sufficiency import EvidenceSufficiencyResult
from review_agent.schemas.obligation import ContractObligation
from review_agent.schemas.obligation_retrieval import ObligationRetrievalBundle
from review_agent.schemas.routing_plan import CatalogMatchResult, ObligationRoutingPlan
from review_agent.services.catalog_alias_match import match_explicit_mentions
from review_agent.services.catalog_matcher import match_obligation_to_catalog
from review_agent.services.catalog_registry import CatalogEntry, indexed_doc_id_set
from review_agent.services.evidence_sufficiency import evaluate_evidence_sufficiency
from review_agent.services.obligation_compare_llm import ipc_item_from_evidence
from review_agent.services.obligation_retrieval import retrieve_for_obligation
from review_agent.services.semantic_routing_planner import _fallback_plan

_FIXTURES_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures"


@dataclass
class RoutingGoldenResult:
    obligation_id: str
    plan: ObligationRoutingPlan
    match: CatalogMatchResult
    retrieval_skipped: bool
    evidence: EvidenceSufficiencyResult
    finding_status: str | None
    candidate_doc_ids: list[str]
    forbidden_violations: list[str] = field(default_factory=list)


def load_catalog_fixture(name: str = "xecurify_policy_catalog.json") -> tuple[list[CatalogEntry], str]:
    raw = json.loads((_FIXTURES_DIR / name).read_text(encoding="utf-8"))
    entries = [
        CatalogEntry(
            document_id=str(item["document_id"]),
            policy_ref=str(item.get("policy_ref") or ""),
            title=str(item.get("title") or ""),
            aliases=[str(a) for a in (item.get("aliases") or [])],
            topics=[str(t) for t in (item.get("topics") or [])],
            summary=str(item.get("summary") or item.get("title") or ""),
        )
        for item in raw.get("policies") or []
    ]
    return entries, str(raw.get("catalog_version") or "v1")


def load_golden_cases() -> list[dict[str, Any]]:
    return json.loads((_FIXTURES_DIR / "routing_golden.json").read_text(encoding="utf-8"))


def _obligation_from_case(case: dict[str, Any]) -> ContractObligation:
    return ContractObligation(
        obligation_id=str(case["obligation_id"]),
        section_id=str(case["section_id"]),
        text=str(case.get("text") or ""),
        is_boilerplate=bool(case.get("is_boilerplate")),
        explicit_policy_mentions=list(case.get("explicit_policy_mentions") or []),
    )


def _plan_from_case(
    ob: ContractObligation,
    catalog: list[CatalogEntry],
    settings: ReviewSettings,
    case: dict[str, Any],
) -> ObligationRoutingPlan:
    if ob.is_boilerplate or not (ob.text or "").strip():
        return _skipped_plan(ob)
    alias = match_explicit_mentions(
        ob.explicit_policy_mentions,
        catalog,
        min_score=settings.routing_alias_min_score,
    )
    if alias and alias.confidence >= settings.routing_alias_min_score:
        return _plan_from_alias(ob, alias)
    mock = case.get("mock_planner")
    if isinstance(mock, dict):
        return ObligationRoutingPlan(
            obligation_id=ob.obligation_id,
            intent=str(mock.get("intent") or ""),
            concepts=[str(c) for c in (mock.get("concepts") or [])],
            search_queries=[str(q) for q in (mock.get("search_queries") or [])],
            explicit_policy_mentions=list(ob.explicit_policy_mentions),
            confidence=float(mock.get("confidence") or 0.5),
            reasoning="golden mock planner",
            routing_source="llm",
        )
    return _fallback_plan(ob)


def _hits_from_case(case: dict[str, Any], tenant_id: str) -> list[RetrievalHit]:
    hits: list[RetrievalHit] = []
    for index, raw in enumerate(case.get("mock_retrieval_hits") or []):
        doc_id = str(raw["document_id"])
        title = str(raw.get("title") or "policy")
        text = str(raw.get("text") or title)
        chunk = IndexedChunk(
            chunk_id=f"{case['obligation_id']}-c{index}",
            document_id=UUID(doc_id),
            tenant_id=tenant_id,
            kind=DocumentKind.POLICY,
            chunk_role=ChunkRole.PARENT,
            section_id="1",
            section_path="1",
            title=title,
            text=text,
        )
        hits.append(
            RetrievalHit(
                parent_chunk=chunk,
                matched_child_ids=[],
                score=float(raw.get("score") or 0.8),
            )
        )
    return hits


def _catalog_hits_from_case(case: dict[str, Any]) -> list[CatalogSearchHit]:
    return [
        CatalogSearchHit(
            document_id=str(item["document_id"]),
            title=str(item.get("title") or ""),
            score=float(item.get("score") or 0.5),
        )
        for item in (case.get("mock_catalog_hits") or [])
    ]


def _forbidden_violations(
    case: dict[str, Any],
    *,
    candidate_doc_ids: list[str],
    finding_status: str | None,
    policy_document_id: str | None,
    entries_by_id: dict[str, CatalogEntry],
) -> list[str]:
    violations: list[str] = []
    forbidden_ids = {str(doc_id) for doc_id in (case.get("forbidden_doc_ids") or [])}
    forbidden_titles = {str(title).lower() for title in (case.get("forbidden_doc_titles") or [])}

    for doc_id in candidate_doc_ids:
        if doc_id in forbidden_ids:
            violations.append(f"candidate:{doc_id}")
        entry = entries_by_id.get(doc_id)
        if entry and any(title in entry.title.lower() for title in forbidden_titles):
            violations.append(f"candidate_title:{doc_id}:{entry.title}")

    if finding_status == ComplianceStatus.NON_COMPLIANT.value and policy_document_id:
        if policy_document_id in forbidden_ids:
            violations.append(f"finding:{policy_document_id}")
        entry = entries_by_id.get(policy_document_id)
        if entry and any(title in entry.title.lower() for title in forbidden_titles):
            violations.append(f"finding_title:{policy_document_id}:{entry.title}")

    return violations


def wrong_policy_compare_count(results: list[RoutingGoldenResult]) -> int:
    return sum(1 for result in results if result.forbidden_violations)


async def run_routing_pipeline_for_obligation(
    case: dict[str, Any],
    *,
    catalog_entries: list[CatalogEntry],
    client: DocumentMCPClient,
    settings: ReviewSettings,
    tenant_id: str = "e2e-demo",
) -> RoutingGoldenResult:
    ob = _obligation_from_case(case)
    plan = _plan_from_case(ob, catalog_entries, settings, case)
    allowed = indexed_doc_id_set(catalog_entries)
    entries_by_id = {entry.document_id: entry for entry in catalog_entries}

    catalog_hits = _catalog_hits_from_case(case)
    if catalog_hits:
        client.search_policy_catalog.return_value = catalog_hits

    match = await match_obligation_to_catalog(
        plan,
        client=client,
        tenant_id=tenant_id,
        catalog_entries=catalog_entries,
        allowed_doc_ids=allowed,
        settings=settings,
    )

    mock_hits = _hits_from_case(case, tenant_id)
    if mock_hits:
        bundle = ObligationRetrievalBundle(
            obligation_id=ob.obligation_id,
            section_id=ob.section_id,
            candidate_doc_ids=list(match.candidate_doc_ids),
            policy_hits=mock_hits,
            queries_used=list(plan.search_queries),
            concepts=list(plan.concepts),
        )
        retrieval_skipped = False
    else:
        bundle = await retrieve_for_obligation(
            client,
            obligation=ob,
            plan=plan,
            match=match,
            tenant_id=tenant_id,
            contract_type="nda",
            policy_type=None,
            settings=settings,
        )
        retrieval_skipped = bool(bundle.skipped_reason)

    evidence = evaluate_evidence_sufficiency(
        obligation=ob,
        plan=plan,
        match=match,
        bundle=bundle,
        settings=settings,
    )

    finding_status: str | None = None
    policy_document_id: str | None = None
    if evidence.decision == "compare":
        finding_status = "COMPARE_READY"
    else:
        item = ipc_item_from_evidence(ob, evidence, plan=plan, match=match)
        finding_status = item.status.value
        policy_document_id = str(item.policy_document_id or "") or None

    violations = _forbidden_violations(
        case,
        candidate_doc_ids=list(match.candidate_doc_ids),
        finding_status=finding_status,
        policy_document_id=policy_document_id,
        entries_by_id=entries_by_id,
    )

    return RoutingGoldenResult(
        obligation_id=ob.obligation_id,
        plan=plan,
        match=match,
        retrieval_skipped=retrieval_skipped,
        evidence=evidence,
        finding_status=finding_status,
        candidate_doc_ids=list(match.candidate_doc_ids),
        forbidden_violations=violations,
    )


async def run_all_golden_cases(
    *,
    settings: ReviewSettings | None = None,
) -> list[RoutingGoldenResult]:
    from unittest.mock import AsyncMock

    cfg = settings or ReviewSettings()
    results: list[RoutingGoldenResult] = []
    default_catalog, _ = load_catalog_fixture()
    for case in load_golden_cases():
        catalog_name = str(case.get("catalog_fixture") or "xecurify_policy_catalog.json")
        catalog = default_catalog if catalog_name == "xecurify_policy_catalog.json" else load_catalog_fixture(catalog_name)[0]
        client = AsyncMock()
        results.append(
            await run_routing_pipeline_for_obligation(
                case,
                catalog_entries=catalog,
                client=client,
                settings=cfg,
            )
        )
    return results
