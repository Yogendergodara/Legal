"""Invariant tests for accuracy-first LLM save/recovery paths (Phase F)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk, RetrievalHit
from document_core.schemas.compliance import ComplianceFinding, ComplianceStatus, Severity
from review_agent.config import ReviewSettings
from review_agent.schemas.obligation import ContractObligation
from review_agent.schemas.evidence_sufficiency import EvidenceSufficiencyResult
from review_agent.schemas.routing_plan import CatalogMatchResult, ObligationRoutingPlan
from review_agent.schemas.section_compare import SectionCompareItem
from review_agent.schemas.obligation_retrieval import ObligationRetrievalBundle
from review_agent.services.accuracy_paths import build_accuracy_paths_summary
from review_agent.services.catalog_registry import CatalogEntry
from review_agent.services.catalog_alias_match import match_explicit_mentions
from review_agent.services.config_advisory import evaluate_config_advisories
from review_agent.services.evidence_sufficiency import evaluate_evidence_sufficiency
from review_agent.services.policy_coverage import apply_coverage_gate
from review_agent.services.section_classifier import classify_sections_batch
from review_agent.services.unclear_recompare import (
    classify_unclear_finding,
    eligible_for_unclear_recompare,
)


def _section(section_id: str, *, title: str = "", text: str = "") -> IndexedChunk:
    return IndexedChunk(
        chunk_id=f"c-{section_id}",
        document_id=uuid4(),
        tenant_id="t1",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id=section_id,
        section_path=section_id,
        title=title or section_id,
        text=text or "Indemnification and liability cap provisions.",
    )


def _policy_hit(*, text: str = "Policy indemnity text.", categories: list[str] | None = None):
    meta = {"categories": categories or ["indemnification"]}
    parent = IndexedChunk(
        chunk_id="p1",
        document_id=uuid4(),
        tenant_id="t1",
        kind=DocumentKind.POLICY,
        chunk_role=ChunkRole.PARENT,
        section_id="p-sec",
        section_path="p-sec",
        title="Policy",
        text=text,
        metadata=meta,
    )
    return RetrievalHit(parent_chunk=parent, score=0.1)


@pytest.mark.asyncio
async def test_coverage_gate_skips_compare_emits_ipc():
    section = _section("5", title="Indemnification")
    off_topic = _policy_hit(text="Unrelated preamble.", categories=["general"])
    settings = ReviewSettings(
        policy_coverage_enabled=True,
        policy_coverage_min_score=0.34,
        policy_coverage_require_specific_overlap=True,
    )
    filtered, ipc, _warnings = apply_coverage_gate(
        [section],
        {section.section_id: [off_topic]},
        {section.section_id: ["indemnification"]},
        settings=settings,
    )
    assert not filtered.get(section.section_id)
    assert len(ipc) == 1
    assert ipc[0].status == ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT


@pytest.mark.asyncio
async def test_lexical_first_skips_llm_for_title_hit(monkeypatch):
    section = _section("1", title="Limitation of Liability", text="Cap shall not exceed fees.")

    async def _boom(*_args, **_kwargs):
        raise AssertionError("classify LLM should not run for title lexical hit")

    monkeypatch.setattr(
        "review_agent.services.section_classifier._classify_batch_llm",
        _boom,
    )
    results = await classify_sections_batch(
        [section],
        settings=ReviewSettings(section_classify_mode="lexical_first"),
    )
    result = results[section.section_id]
    assert result.classify_warning.startswith("lexical_first=")


def test_alias_skips_planner_for_explicit_mention():
    catalog = [
        CatalogEntry(
            document_id="doc-1",
            policy_ref="atlassian-msa",
            title="Atlassian MSA",
            aliases=["Atlassian Master Services Agreement"],
            topics=[],
            summary="Atlassian MSA",
        )
    ]
    alias = match_explicit_mentions(
        ["Atlassian Master Services Agreement"],
        catalog,
        min_score=0.92,
    )
    assert alias is not None
    assert alias.confidence >= 0.92


def test_evidence_insufficient_no_compare_decision():
    obligation = ContractObligation(
        obligation_id="o1",
        section_id="1",
        text="Vendor shall maintain insurance.",
    )
    plan = ObligationRoutingPlan(
        obligation_id="o1",
        confidence=0.9,
        routing_source="llm",
    )
    match = CatalogMatchResult(obligation_id="o1", route_decision="compare")
    weak_hit = _policy_hit(text="Unrelated.", categories=["general"])
    bundle = ObligationRetrievalBundle(
        obligation_id="o1",
        section_id="1",
        policy_hits=[weak_hit],
    )
    result = evaluate_evidence_sufficiency(
        obligation=obligation,
        plan=plan,
        match=match,
        bundle=bundle,
        settings=ReviewSettings(
            evidence_min_score=0.35,
            evidence_min_concept_overlap=0.25,
        ),
    )
    assert result.decision != "compare"


def test_coverage_gate_ipc_eligible_for_recompare():
    finding = ComplianceFinding(
        finding_id="f1",
        dimension_id="5:x",
        dimension_label="Indemnification",
        status=ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
        severity=Severity.INFO,
        contract_section_id="5",
        rationale="Retrieved policies were not sufficiently on-topic (coverage=0.00).",
        metadata={"gap_type": "coverage_gate_ipc", "source": "coverage_gate"},
    )
    assert classify_unclear_finding(finding) == "coverage_gate_ipc"
    assert eligible_for_unclear_recompare(finding)


def test_f1_off_advisory_when_coverage_disabled():
    advisories = evaluate_config_advisories(
        ReviewSettings(policy_coverage_enabled=False),
        tenant_id="demo",
    )
    assert any(a.rule_id == "F1-off" for a in advisories)


def test_accuracy_paths_summary_from_stats():
    summary = build_accuracy_paths_summary(
        {
            "classify_lexical_skipped": 10,
            "classify_llm_sections": 5,
            "coverage_gate_ipc_count": 2,
            "obligation_alias_hit_count": 3,
            "routing_planner_calls": 4,
            "obligation_pipeline_funnel": {
                "extracted": 50,
                "compare_queued": 10,
                "compare_pre_ipc": 40,
            },
            "obligation_evidence_skip_by_reason": {
                "low_relevance_score": 12,
                "routing_or_skip": 8,
            },
        },
        {
            "unclear_recompared": 2,
            "gap_recompare_batches": 1,
            "coverage_gate_recompare_attempted": 1,
            "coverage_gate_recompare_resolved": 1,
        },
        reviewable_sections=20,
        settings=ReviewSettings(),
    )
    assert summary["save"]["classify_lexical_skipped"] == 10
    assert summary["save"]["obligation_evidence_ipc"] == 40
    assert summary["save"]["planner_calls_avoided_estimate"] == 3
    assert summary["recover"]["unclear_recompared"] == 2
    assert "saved_classify" in summary["net_story"]
