"""Build obligation routing audit blobs for findings and artifacts (Phase R7)."""

from __future__ import annotations

from typing import Any

from review_agent.schemas.evidence_sufficiency import EvidenceSufficiencyResult
from review_agent.schemas.obligation_retrieval import ObligationRetrievalBundle
from review_agent.schemas.routing_plan import CatalogMatchResult, ObligationRoutingPlan


def _title_for_doc(document_id: str, indexed_policies: list[dict]) -> str:
    for entry in indexed_policies:
        if str(entry.get("document_id") or "") == document_id:
            return str(entry.get("title") or "").strip()
    return ""


def build_routing_audit(
    *,
    obligation_id: str,
    section_id: str,
    plan: ObligationRoutingPlan,
    match: CatalogMatchResult,
    bundle: ObligationRetrievalBundle | None,
    evidence: EvidenceSufficiencyResult | None,
    indexed_policies: list[dict] | None = None,
) -> dict[str, Any]:
    policies = indexed_policies or []
    candidate_ids = list(match.candidate_doc_ids or [])
    return {
        "obligation_id": obligation_id,
        "section_id": section_id,
        "routing_source": plan.routing_source,
        "routing_confidence": plan.confidence,
        "candidate_doc_ids": candidate_ids,
        "candidate_titles": [_title_for_doc(doc_id, policies) for doc_id in candidate_ids],
        "candidate_scores": dict(match.candidate_scores or {}),
        "rejected": list(match.rejected or []),
        "queries_used": list(
            (bundle.queries_used if bundle else [])
            or match.queries_used
            or plan.search_queries
        ),
        "catalog_match_source": match.routing_source,
        "evidence_decision": evidence.decision if evidence else "",
        "evidence_reason": evidence.reason if evidence else "",
        "hit_count": evidence.hit_count if evidence else len((bundle.policy_hits if bundle else []) or []),
        "expand_round": evidence.expand_round if evidence else 0,
    }
