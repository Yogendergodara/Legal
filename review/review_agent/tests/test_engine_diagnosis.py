"""Tests for canonical engine_diagnosis assembly (Phase P5)."""

from __future__ import annotations

import uuid

from document_core.schemas.compliance import ComplianceFinding, ComplianceStatus, Severity
from review_agent.services.engine_diagnosis import (
    ENGINE_DIAGNOSIS_VERSION,
    build_engine_diagnosis,
)
from review_agent.services.review_artifact import build_review_artifact


def _obligation_stats() -> dict:
    return {
        "obligation_ipc_findings": 72,
        "obligation_compare_count": 8,
        "obligation_evidence_skip_by_reason": {"routing_or_skip": 46, "low_relevance_score": 12},
        "obligation_pipeline_funnel": {
            "extracted": 80,
            "compare_queued": 40,
            "compare_pre_ipc": 40,
            "llm_batches": 5,
            "llm_batches_failed": 1,
            "skip_by_reason": {"routing_or_skip": 46},
        },
        "routing_summary": {
            "obligation_count": 80,
            "ipc_rate": 0.9,
            "compare_rate": 0.1,
            "wrong_policy_blocked": 0,
        },
        "llm_rate_limit_events": 3,
        "llm_batches_failed": 0,
    }


def test_engine_diagnosis_obligation_path():
    stats = _obligation_stats()
    review_confidence = {
        "sections_total": 27,
        "ipc_section_pct": 74.1,
        "inconclusive_section_pct": 10.0,
        "confident_section_pct": 15.9,
        "downgrade_quote_validate": 0,
        "downgrade_grounding": 0,
    }
    state = {
        "section_coverage": {"reviewable_count": 27},
        "section_compare_items": [{"section_id": "1"}],
        "failed_sections": [
            {
                "section_id": "10.1",
                "error_code": "retrieval_zero_hit",
                "stage": "retrieve",
            }
        ],
        "compliance_stats": stats,
        "final_verify_stats": {"compare_omitted_recovered": 15, "unclear_recompared": 2},
    }
    diagnosis = build_engine_diagnosis(
        state=state,
        findings=[],
        compliance_stats=stats,
        final_verify_stats=state["final_verify_stats"],
        gap_status_summary={"inconclusive_playbook_gap": 3},
        review_confidence=review_confidence,
    )

    assert diagnosis["schema_version"] == ENGINE_DIAGNOSIS_VERSION
    assert diagnosis["pipeline_mode"] == "hybrid"
    assert diagnosis["obligation_pipeline"]["funnel"]["compare_queued"] == 40
    assert diagnosis["ipc_summary"]["obligation_ipc_rate"] == 0.9
    assert diagnosis["ipc_summary"]["skip_by_reason"]["routing_or_skip"] == 46
    assert diagnosis["ipc_summary"]["skip_by_reason"]["low_relevance_score"] == 12
    assert diagnosis["section_pipeline"]["sections_reviewable"] == 27
    assert diagnosis["section_pipeline"]["retrieval_zero_hit_section_ids"] == ["10.1"]
    assert diagnosis["recovery"]["final_verify"]["compare_omitted_recovered"] == 15
    assert diagnosis["recovery"]["gap_status_summary"]["compare_omitted_recovered"] == 15
    assert diagnosis["resilience"]["obligation_compare_batches_failed"] == 1


def test_engine_diagnosis_section_only():
    stats = {
        "compliance_mode": "section_first",
        "sections_total": 6,
        "compare_items": 6,
        "retrieval_zero_hit_sections": 0,
        "coverage_gate_ipc_count": 0,
        "compare_hit_selection": {"category_aligned_sections": 6},
        "llm_rate_limit_events": 0,
        "llm_batches_failed": 0,
    }
    findings = [
        ComplianceFinding(
            finding_id="f1",
            dimension_id="1:x",
            dimension_label="Cap",
            status=ComplianceStatus.INCONCLUSIVE,
            severity=Severity.INFO,
            contract_section_id="1",
            rationale="Playbook gap.",
            metadata={"source": "playbook_compare"},
        ),
    ]
    review_confidence = {
        "sections_total": 6,
        "ipc_section_pct": 0.0,
        "inconclusive_section_pct": 16.7,
        "confident_section_pct": 83.3,
        "downgrade_quote_validate": 0,
        "downgrade_grounding": 0,
    }
    state = {
        "section_coverage": {"reviewable_count": 6},
        "compliance_stats": stats,
        "final_verify_stats": {},
    }
    diagnosis = build_engine_diagnosis(
        state=state,
        findings=findings,
        compliance_stats=stats,
        final_verify_stats={},
        gap_status_summary={},
        review_confidence=review_confidence,
    )

    assert diagnosis["pipeline_mode"] == "section_first"
    assert "obligation_pipeline" not in diagnosis
    assert diagnosis["section_pipeline"]["sections_compared"] == 6
    assert diagnosis["ipc_summary"]["section_ipc_pct"] == 0.0
    assert diagnosis["review_confidence"]["sections_total"] == 6
    assert "accuracy_paths" in diagnosis
    assert diagnosis["accuracy_paths"]["save"]["classify_lexical_skipped"] == 0
    assert "config_pressure" in diagnosis["infrastructure"]
    assert diagnosis["infrastructure"]["config_pressure"]["unclear_recompare_cap_mode"] == "adaptive"


def test_artifact_matches_metadata_diagnosis():
    stats = _obligation_stats()
    review_confidence = {"sections_total": 5, "ipc_section_pct": 20.0}
    diagnosis = build_engine_diagnosis(
        state={"compliance_stats": stats, "final_verify_stats": {}},
        findings=[],
        compliance_stats=stats,
        final_verify_stats={},
        gap_status_summary={},
        review_confidence=review_confidence,
    )
    enriched_stats = {**stats, "engine_diagnosis": diagnosis, "review_confidence": review_confidence}
    state = {
        "tenant_id": "demo",
        "thread_id": "run-1",
        "contract_title": "MSA",
        "contract_document_id": str(uuid.uuid4()),
        "compliance_stats": stats,
    }
    artifact = build_review_artifact(
        state,
        engine_diagnosis=diagnosis,
        compliance_stats=enriched_stats,
    )
    assert artifact.engine_diagnosis == diagnosis
    assert artifact.compliance_stats.get("engine_diagnosis") == diagnosis
    assert artifact.compliance_stats.get("routing_summary") == stats["routing_summary"]


def test_no_data_loss_compliance_stats_keys():
    stats = _obligation_stats()
    diagnosis = build_engine_diagnosis(
        state={"compliance_stats": stats},
        findings=[],
        compliance_stats=stats,
        final_verify_stats={},
        gap_status_summary={},
        review_confidence={},
    )
    enriched = {**stats, "engine_diagnosis": diagnosis}
    for key in ("routing_summary", "obligation_ipc_findings", "obligation_compare_count"):
        assert key in enriched
        assert enriched[key] == stats[key]


def test_ipc_summary_skip_by_reason_canonical_no_double_count():
    evidence_skip = {"routing_or_skip": 22, "evidence_sufficient": 42}
    stats = {
        "obligation_evidence_skip_by_reason": evidence_skip,
        "obligation_pipeline_funnel": {"skip_by_reason": dict(evidence_skip)},
    }
    diagnosis = build_engine_diagnosis(
        state={},
        findings=[],
        compliance_stats=stats,
        final_verify_stats={},
        gap_status_summary={},
        review_confidence={},
    )
    skip = diagnosis["ipc_summary"]["skip_by_reason"]
    assert skip["routing_or_skip"] == 22
    assert skip["evidence_sufficient"] == 42


def test_ipc_summary_includes_cutover_mode():
    stats = {
        "runtime_settings": {"obligation_section_cutover_mode": "ipc_fallback"},
    }
    diagnosis = build_engine_diagnosis(
        state={},
        findings=[],
        compliance_stats=stats,
        final_verify_stats={},
        gap_status_summary={},
        review_confidence={},
    )
    assert diagnosis["ipc_summary"]["cutover_mode"] == "ipc_fallback"


def test_planner_fallback_ipc_count_from_state():
    state = {
        "obligation_routing_by_id": {
            "o1": {"routing_source": "planner_fallback"},
            "o2": {"routing_source": "planner_fallback"},
            "o3": {"routing_source": "llm"},
        },
        "obligation_evidence_by_id": {
            "o1": {"decision": "ipc"},
            "o2": {"decision": "compare"},
            "o3": {"decision": "ipc"},
        },
    }
    diagnosis = build_engine_diagnosis(
        state=state,
        findings=[],
        compliance_stats={},
        final_verify_stats={},
        gap_status_summary={},
        review_confidence={},
    )
    assert diagnosis["ipc_summary"]["planner_fallback_ipc_count"] == 1


def test_resilience_breaker_open_events():
    stats = {
        "breaker_open_events": 2,
        "breaker_open_events_llm": 1,
        "breaker_open_events_mcp": 1,
    }
    diagnosis = build_engine_diagnosis(
        state={},
        findings=[],
        compliance_stats=stats,
        final_verify_stats={},
        gap_status_summary={},
        review_confidence={},
    )
    assert diagnosis["resilience"]["breaker_open_events"] == 2
    assert diagnosis["resilience"]["breaker_open_events_llm"] == 1
    assert diagnosis["resilience"]["breaker_open_events_mcp"] == 1


def test_baseline_interpretation_when_profile_set(monkeypatch):
    from review_agent.config import get_settings

    stats = _obligation_stats()
    stats["non_compliant_count"] = 6
    stats["obligation_compare_llm_batches"] = 8

    cfg = get_settings().model_copy(update={"baseline_profile": "atlassian_v1"})
    monkeypatch.setattr("review_agent.services.engine_diagnosis.get_settings", lambda: cfg)
    diagnosis = build_engine_diagnosis(
        state={"compliance_stats": stats, "final_verify_stats": {}},
        findings=[],
        compliance_stats=stats,
        final_verify_stats={},
        gap_status_summary={},
        review_confidence={},
    )
    interp = diagnosis.get("baseline_interpretation") or {}
    assert interp.get("baseline_id") == "atlassian_v1"
    assert "funnel_story" in interp
    assert interp["primary_accuracy"]["status"] == "ok"
