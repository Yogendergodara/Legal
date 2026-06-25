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
