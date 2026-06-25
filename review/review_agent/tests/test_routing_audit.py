"""Tests for routing audit builder (Phase R7)."""

from __future__ import annotations

from review_agent.schemas.evidence_sufficiency import EvidenceSufficiencyResult
from review_agent.schemas.obligation_retrieval import ObligationRetrievalBundle
from review_agent.schemas.routing_plan import CatalogMatchResult, ObligationRoutingPlan
from review_agent.services.routing_audit import build_routing_audit


def test_audit_blob_complete():
    doc_id = "11111111-1111-1111-1111-111111111111"
    audit = build_routing_audit(
        obligation_id="2.3-o0",
        section_id="2.3",
        plan=ObligationRoutingPlan(
            obligation_id="2.3-o0",
            routing_source="registry_alias",
            confidence=1.0,
            search_queries=["security practices"],
        ),
        match=CatalogMatchResult(
            obligation_id="2.3-o0",
            candidate_doc_ids=[doc_id],
            candidate_scores={doc_id: 1.0},
            routing_source="registry_alias",
        ),
        bundle=ObligationRetrievalBundle(
            obligation_id="2.3-o0",
            section_id="2.3",
            queries_used=["security practices"],
            candidate_doc_ids=[doc_id],
        ),
        evidence=EvidenceSufficiencyResult(
            obligation_id="2.3-o0",
            decision="compare",
            reason="evidence_sufficient",
            hit_count=2,
        ),
        indexed_policies=[{"document_id": doc_id, "title": "Security Practices Policy"}],
    )
    assert audit["routing_source"] == "registry_alias"
    assert audit["candidate_doc_ids"] == [doc_id]
    assert audit["candidate_titles"] == ["Security Practices Policy"]
    assert audit["evidence_decision"] == "compare"
    assert audit["hit_count"] == 2
