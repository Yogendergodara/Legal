"""Tests for RG-1 coverage-gate recovery and OB-1 ops fixes."""

import pytest

from document_core.schemas.compliance import ComplianceFinding, ComplianceStatus, Severity
from review_agent.config import ReviewSettings
from review_agent.schemas.section_compare import SectionCompareItem
from review_agent.services.catalog_alias_match import match_explicit_mentions
from review_agent.services.catalog_registry import CatalogEntry
from review_agent.services.engine_diagnosis import build_engine_diagnosis
from review_agent.services.finding_dedupe import cap_compare_items_by_section
from review_agent.services.section_merge import section_items_to_findings
from review_agent.services.unclear_recompare import (
    classify_unclear_finding,
    eligible_for_unclear_recompare,
)


def test_coverage_gate_ipc_tagged():
    items = [
        SectionCompareItem(
            section_id="5",
            dimension_label="Governing Law",
            status=ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
            severity=Severity.INFO,
            rationale=(
                "Retrieved policies were not sufficiently on-topic for this contract section "
                "(coverage=0.00, reason=no_specific_category_overlap)."
            ),
        )
    ]
    findings = section_items_to_findings(items, dedupe=False)
    assert findings[0].metadata.get("gap_type") == "coverage_gate_ipc"
    assert findings[0].metadata.get("source") == "coverage_gate"


def test_coverage_gate_ipc_eligible():
    finding = ComplianceFinding(
        finding_id="f-cov",
        dimension_id="5:general",
        dimension_label="Governing Law",
        status=ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
        severity=Severity.INFO,
        contract_section_id="5",
        rationale=(
            "Retrieved policies were not sufficiently on-topic for this contract section "
            "(coverage=0.00, reason=no_specific_category_overlap)."
        ),
        metadata={"gap_type": "coverage_gate_ipc", "source": "coverage_gate"},
    )
    assert classify_unclear_finding(finding) == "coverage_gate_ipc"
    assert eligible_for_unclear_recompare(finding)


def test_true_no_policy_ipc_not_coverage_gate():
    finding = ComplianceFinding(
        finding_id="f-np",
        dimension_id="5:general",
        dimension_label="Gap",
        status=ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
        severity=Severity.INFO,
        contract_section_id="5",
        rationale="No relevant policy sections were retrieved.",
        metadata={"source": "playbook_compare"},
    )
    assert classify_unclear_finding(finding) == "inconclusive_other"
    assert not eligible_for_unclear_recompare(finding)


def test_coverage_gate_recompare_disabled(monkeypatch):
    finding = ComplianceFinding(
        finding_id="f-cov",
        dimension_id="5:general",
        dimension_label="Governing Law",
        status=ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
        contract_section_id="5",
        rationale=(
            "Retrieved policies were not sufficiently on-topic for this contract section "
            "(coverage=0.00, reason=no_specific_category_overlap)."
        ),
        metadata={"gap_type": "coverage_gate_ipc"},
    )
    monkeypatch.setattr(
        "review_agent.config.get_settings",
        lambda: ReviewSettings(final_verify_coverage_gate_recompare_enabled=False),
    )
    assert not eligible_for_unclear_recompare(finding)


def test_diagnosis_skip_not_doubled():
    evidence_skip = {"routing_or_skip": 22, "evidence_sufficient": 42}
    stats = {
        "obligation_evidence_skip_by_reason": evidence_skip,
        "obligation_pipeline_funnel": {"skip_by_reason": dict(evidence_skip)},
        "obligation_ipc_findings": 67,
        "obligation_compare_count": 18,
    }
    diagnosis = build_engine_diagnosis(
        state={},
        findings=[],
        compliance_stats=stats,
        final_verify_stats={},
        gap_status_summary={},
        review_confidence={},
    )
    assert diagnosis["ipc_summary"]["skip_by_reason"]["routing_or_skip"] == 22
    assert diagnosis["ipc_summary"]["skip_by_reason"]["evidence_sufficient"] == 42


def test_cap_never_drops_important_nc():
    items = [
        SectionCompareItem(
            section_id="s1",
            dimension_label=f"NC {idx}",
            status=ComplianceStatus.NON_COMPLIANT,
            severity=Severity.IMPORTANT,
            contract_quote=f"distinct gap text number {idx} in contract section",
            policy_quote="Policy requires twelve month cap.",
            rationale=f"Gap {idx} below policy minimum.",
        )
        for idx in range(5)
    ]
    capped, removed, _warnings = cap_compare_items_by_section(items, 4)
    assert removed == 0
    assert len(capped) == 5


def test_alias_token_fallback():
    catalog = [
        CatalogEntry(
            document_id="doc-1",
            policy_ref="security-practices",
            title="Security Practices Policy",
            aliases=["Security Practices"],
            topics=[],
            summary="Security Practices Policy",
        )
    ]
    match = match_explicit_mentions(
        ["Security Practices"],
        catalog,
        min_score=0.92,
        token_fallback=True,
    )
    assert match is not None
    assert match.document_id == "doc-1"


def test_quote_validated_at_compare_metadata():
    items = [
        SectionCompareItem(
            section_id="s1",
            status=ComplianceStatus.NON_COMPLIANT,
            severity=Severity.CRITICAL,
            contract_quote="liability is unlimited",
            policy_quote="liability cap required",
            rationale="Contract exceeds policy cap.",
        )
    ]
    findings = section_items_to_findings(items, dedupe=False)
    assert findings[0].metadata.get("quote_validated_at_compare") is True


def test_ipc_fallback_section_ids():
    from review_agent.graph.section_compare_nodes import _ipc_fallback_section_ids

    state = {
        "obligation_findings": [
            ComplianceFinding(
                finding_id="f1",
                dimension_id="2.3:o0",
                dimension_label="Notify",
                status=ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
                contract_section_id="2.3",
                rationale="IPC",
                metadata={"obligation_id": "2.3-o0"},
            ).model_dump(mode="json"),
            ComplianceFinding(
                finding_id="f2",
                dimension_id="10.1:o0",
                dimension_label="Law",
                status=ComplianceStatus.NON_COMPLIANT,
                contract_section_id="10.1",
                contract_quote="governed by delaware",
                policy_quote="governed by california",
                rationale="Mismatch",
                metadata={"obligation_id": "10.1-o0"},
            ).model_dump(mode="json"),
        ]
    }
    ipc_only = _ipc_fallback_section_ids(state)
    assert ipc_only == {"2.3"}
    assert "10.1" not in ipc_only


def test_ipc_fallback_includes_inconclusive_section():
    from review_agent.graph.section_compare_nodes import _ipc_fallback_section_ids

    state = {
        "obligation_findings": [
            ComplianceFinding(
                finding_id="f1",
                dimension_id="2.3:o0",
                dimension_label="Notify",
                status=ComplianceStatus.INCONCLUSIVE,
                contract_section_id="2.3",
                rationale="Compare failed",
                metadata={"obligation_id": "2.3-o0"},
            ).model_dump(mode="json"),
            ComplianceFinding(
                finding_id="f2",
                dimension_id="2.3:o1",
                dimension_label="Timeline",
                status=ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
                contract_section_id="2.3",
                rationale="IPC",
                metadata={"obligation_id": "2.3-o1"},
            ).model_dump(mode="json"),
        ]
    }
    assert _ipc_fallback_section_ids(state) == {"2.3"}


def test_ipc_fallback_excludes_compliant_mixed():
    from review_agent.graph.section_compare_nodes import _ipc_fallback_section_ids

    state = {
        "obligation_findings": [
            ComplianceFinding(
                finding_id="f1",
                dimension_id="2.3:o0",
                dimension_label="Notify",
                status=ComplianceStatus.INCONCLUSIVE,
                contract_section_id="2.3",
                rationale="Compare failed",
                metadata={"obligation_id": "2.3-o0"},
            ).model_dump(mode="json"),
            ComplianceFinding(
                finding_id="f2",
                dimension_id="2.3:o1",
                dimension_label="Timeline",
                status=ComplianceStatus.COMPLIANT,
                contract_section_id="2.3",
                contract_quote="notify",
                policy_quote="notify",
                rationale="Match",
                metadata={"obligation_id": "2.3-o1"},
            ).model_dump(mode="json"),
        ]
    }
    assert "2.3" not in _ipc_fallback_section_ids(state)


def test_ipc_fallback_section_ids_from_evidence():
    from review_agent.graph.section_compare_nodes import _ipc_fallback_section_ids_from_evidence
    from review_agent.schemas.evidence_sufficiency import EvidenceSufficiencyResult

    state = {
        "obligations": [
            {"obligation_id": "2.3-o0", "section_id": "2.3", "text": "Notify"},
            {"obligation_id": "10.1-o0", "section_id": "10.1", "text": "Law"},
        ],
        "obligation_evidence_by_id": {
            "2.3-o0": EvidenceSufficiencyResult(
                obligation_id="2.3-o0",
                decision="ipc",
                reason="insufficient_hits",
            ).model_dump(mode="json"),
            "10.1-o0": EvidenceSufficiencyResult(
                obligation_id="10.1-o0",
                decision="compare",
                reason="evidence_sufficient",
            ).model_dump(mode="json"),
        },
    }
    ipc_only = _ipc_fallback_section_ids_from_evidence(state)
    assert ipc_only == {"2.3"}
    assert "10.1" not in ipc_only


def test_ipc_fallback_for_cutover_prefers_evidence_regardless_of_pipeline_mode():
    """PG-2: evidence-based ipc_fallback when obligation_evidence_by_id is populated."""
    from review_agent.config import ReviewSettings
    from review_agent.graph.section_compare_nodes import _ipc_fallback_section_ids_for_cutover
    from review_agent.schemas.evidence_sufficiency import EvidenceSufficiencyResult

    state = {
        "obligations": [
            {"obligation_id": "2.3-o0", "section_id": "2.3", "text": "Notify"},
            {"obligation_id": "10.1-o0", "section_id": "10.1", "text": "Law"},
        ],
        "obligation_evidence_by_id": {
            "2.3-o0": EvidenceSufficiencyResult(
                obligation_id="2.3-o0",
                decision="ipc",
                reason="insufficient_hits",
            ).model_dump(mode="json"),
            "10.1-o0": EvidenceSufficiencyResult(
                obligation_id="10.1-o0",
                decision="compare",
                reason="evidence_sufficient",
            ).model_dump(mode="json"),
        },
        "obligation_findings": [
            {
                "finding_id": "obl-10.1",
                "dimension_id": "10.1:obl",
                "dimension_label": "Law",
                "status": "INSUFFICIENT_POLICY_CONTEXT",
                "severity": "INFO",
                "contract_section_id": "10.1",
                "rationale": "would differ if obligation path used",
            }
        ],
    }
    serial = ReviewSettings(review_pipeline_mode="serial")
    parallel = ReviewSettings(review_pipeline_mode="parallel_hybrid")
    assert _ipc_fallback_section_ids_for_cutover(state, serial) == {"2.3"}
    assert _ipc_fallback_section_ids_for_cutover(state, parallel) == {"2.3"}
