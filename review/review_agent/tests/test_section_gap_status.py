"""Tests for gap status semantics (Phase 22 P3)."""

from uuid import uuid4

from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk
from document_core.schemas.compliance import ComplianceFinding, ComplianceStatus, Severity
from review_agent.config import ReviewSettings
from review_agent.services.section_gap_status import (
    is_boilerplate_section,
    is_non_substantive_section,
    resolve_gap_finding_status,
    upgrade_substantive_gap_finding,
)


def _section(title: str, text: str = "x" * 50, section_id: str = "s1") -> IndexedChunk:
    return IndexedChunk(
        chunk_id="c1",
        document_id=uuid4(),
        tenant_id="demo",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id=section_id,
        section_path=section_id,
        title=title,
        text=text,
    )


def test_parties_boilerplate_classify():
    section = _section("Parties and Effective Date", "Acme and Vendor enter this Agreement.")
    assert is_boilerplate_section(section) is True
    assert is_non_substantive_section(section) is True


def test_boilerplate_definitions_insufficient():
    section = _section("Definitions", "Party means the signatory to this agreement.")
    assert is_boilerplate_section(section) is True
    status, outcome, _suffix = resolve_gap_finding_status(section, gap_type="no_policy")
    assert status == ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT
    assert outcome == "boilerplate"


def test_substantive_liability_inconclusive():
    section = _section(
        "Limitation of Liability",
        "Total liability shall not exceed fees paid in twelve months.",
    )
    assert is_boilerplate_section(section) is False
    status, outcome, suffix = resolve_gap_finding_status(
        section,
        gap_type="no_policy",
        categories=["liability"],
    )
    assert status == ComplianceStatus.INCONCLUSIVE
    assert outcome == "playbook_gap"
    assert "liability" in suffix


def test_compare_omitted_pipeline_incomplete():
    section = _section("Indemnification", "Vendor shall indemnify customer.")
    status, outcome, _suffix = resolve_gap_finding_status(section, gap_type="compare_omitted")
    assert status == ComplianceStatus.INCONCLUSIVE
    assert outcome == "pipeline_incomplete"


def test_legacy_flag_restores_insufficient():
    section = _section("Limitation of Liability", "Liability cap applies.")
    settings = ReviewSettings(gap_status_substantive_inconclusive=False)
    status, _outcome, _suffix = resolve_gap_finding_status(
        section,
        gap_type="no_policy",
        settings=settings,
    )
    assert status == ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT


def test_gap_llm_upgrades_substantive_insufficient():
    section = _section(
        "Limitation of Liability",
        "Vendor liability is unlimited.",
    )
    finding = ComplianceFinding(
        finding_id="f1",
        dimension_id="s1:final_gap",
        dimension_label="Limitation of Liability",
        status=ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
        contract_section_id="s1",
        rationale="No matching playbook.",
        metadata={"final_verify": "gap_llm", "gap_type": "no_policy"},
    )
    upgraded = upgrade_substantive_gap_finding(finding, section)
    assert upgraded.status == ComplianceStatus.INCONCLUSIVE
    assert upgraded.metadata.get("review_outcome") == "playbook_gap"
    assert upgraded.metadata.get("status_upgraded_from") == "INSUFFICIENT_POLICY_CONTEXT"


def test_gap_llm_keeps_boilerplate_insufficient():
    section = _section("Notices", "Notices shall be sent by certified mail.")
    finding = ComplianceFinding(
        finding_id="f1",
        dimension_id="s1:final_gap",
        dimension_label="Notices",
        status=ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
        severity=Severity.INFO,
        contract_section_id="s1",
        rationale="Standard notices.",
        metadata={"final_verify": "gap_llm", "gap_type": "no_policy"},
    )
    upgraded = upgrade_substantive_gap_finding(finding, section)
    assert upgraded.status == ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT
    assert upgraded.metadata.get("review_outcome") == "boilerplate"


def test_numbered_notices_is_boilerplate():
    section = _section("10.5 Notices", "Notices shall be delivered by certified mail.")
    assert is_boilerplate_section(section) is True


def test_numbered_severability_is_boilerplate():
    section = _section("10.3 Severability", "If any provision is invalid, the remainder survives.")
    assert is_boilerplate_section(section) is True


def test_numbered_entire_agreement_is_boilerplate():
    section = _section("10.1 Entire Agreement", "This Agreement constitutes the entire agreement.")
    assert is_boilerplate_section(section) is True


def test_assignment_is_boilerplate():
    section = _section("Assignment", "Neither party may assign without consent.")
    assert is_boilerplate_section(section) is True


def test_governing_law_not_boilerplate():
    section = _section("Governing Law", "This Agreement is governed by Delaware law.")
    assert is_boilerplate_section(section) is False
