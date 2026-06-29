"""Tests for routing validation guards (Phase R7)."""

from __future__ import annotations

from uuid import uuid4

from document_core.schemas.compliance import ComplianceStatus
from review_agent.schemas.obligation import ContractObligation
from review_agent.schemas.obligation_compare import ObligationCompareItem
from review_agent.services.routing_validation import validate_obligation_compare_items


def test_validate_invented_policy_blocked():
    allowed = {str(uuid4())}
    candidate = str(uuid4())
    item = ObligationCompareItem(
        obligation_id="x-o0",
        section_id="2.3",
        policy_document_id=str(uuid4()),
        status=ComplianceStatus.NON_COMPLIANT,
        rationale="Material deviation from policy requirement on encryption.",
    )
    validated, warnings, rejected = validate_obligation_compare_items(
        [item],
        obligations_by_id={
            "x-o0": ContractObligation(obligation_id="x-o0", section_id="2.3", text="text")
        },
        allowed_doc_ids=allowed,
        candidate_doc_ids_by_obligation={"x-o0": {candidate}},
    )
    assert rejected == 1
    assert validated[0].status == ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT
    assert warnings


def test_tenant_scoped_policy_allowed_outside_candidate_fence():
    """OB-03: policy in request scope but outside catalog fence must not be rejected."""
    scoped_doc = str(uuid4())
    other_candidate = str(uuid4())
    item = ObligationCompareItem(
        obligation_id="x-o0",
        section_id="2.3",
        policy_document_id=scoped_doc,
        status=ComplianceStatus.NON_COMPLIANT,
        rationale="Obligation conflicts with scoped policy requirement on data retention.",
    )
    validated, warnings, rejected = validate_obligation_compare_items(
        [item],
        obligations_by_id={
            "x-o0": ContractObligation(
                obligation_id="x-o0",
                section_id="2.3",
                text="Customer shall retain data for seven years.",
            )
        },
        allowed_doc_ids={scoped_doc},
        candidate_doc_ids_by_obligation={"x-o0": {other_candidate}},
    )
    assert rejected == 0
    assert validated[0].status == ComplianceStatus.NON_COMPLIANT
    assert not warnings


def test_boilerplate_validation_blocks_non_compliant():
    item = ObligationCompareItem(
        obligation_id="10.1-o0",
        section_id="10.1",
        status=ComplianceStatus.NON_COMPLIANT,
        rationale="Incorrect governing law jurisdiction selected in contract clause.",
    )
    validated, _, rejected = validate_obligation_compare_items(
        [item],
        obligations_by_id={
            "10.1-o0": ContractObligation(
                obligation_id="10.1-o0",
                section_id="10.1",
                text="Wyoming law.",
                is_boilerplate=True,
            )
        },
        allowed_doc_ids=set(),
        candidate_doc_ids_by_obligation={},
    )
    assert rejected == 1
    assert validated[0].status == ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT


def test_unused_policy_term_blocks_non_compliant():
    item = ObligationCompareItem(
        obligation_id="1-o0",
        section_id="1",
        dimension_label="Definition of Sensitive Data",
        status=ComplianceStatus.NON_COMPLIANT,
        rationale="Contract section does not define Sensitive Data while policy explicitly defines it.",
    )
    validated, _, rejected = validate_obligation_compare_items(
        [item],
        obligations_by_id={
            "1-o0": ContractObligation(
                obligation_id="1-o0",
                section_id="1",
                text="For purposes of this Agreement, the following terms shall have the meanings set forth below:",
            )
        },
        allowed_doc_ids=set(),
        candidate_doc_ids_by_obligation={},
    )
    assert rejected == 1
    assert validated[0].status == ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT
    assert "unused_policy_term" in validated[0].rationale


def test_used_policy_term_allows_non_compliant():
    item = ObligationCompareItem(
        obligation_id="1.1-o0",
        section_id="1.1",
        dimension_label="Definition of Sensitive Data",
        status=ComplianceStatus.NON_COMPLIANT,
        rationale="Sensitive Data definition in contract is narrower than policy requires.",
    )
    validated, _, rejected = validate_obligation_compare_items(
        [item],
        obligations_by_id={
            "1.1-o0": ContractObligation(
                obligation_id="1.1-o0",
                section_id="1.1",
                text='Sensitive Data means any regulated personal or financial information.',
            )
        },
        allowed_doc_ids=set(),
        candidate_doc_ids_by_obligation={},
    )
    assert rejected == 0
    assert validated[0].status == ComplianceStatus.NON_COMPLIANT
