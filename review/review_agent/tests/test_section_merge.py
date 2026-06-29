"""Tests for section-first finding merge."""

from uuid import uuid4

from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk
from document_core.schemas.compliance import ComplianceStatus, Severity
from review_agent.config import ReviewSettings
from review_agent.schemas.section_compare import SectionCompareItem
from review_agent.schemas.section_retrieval import SectionRetrievalBundle
from review_agent.services.section_merge import merge_section_findings, section_items_to_findings
from review_agent.services.playbook_context import PlaybookHints


def test_merge_dedupes_compare_items():
    items = [
        SectionCompareItem(
            section_id="s1",
            policy_document_id="550e8400-e29b-41d4-a716-446655440000",
            dimension_label="Liability",
            status=ComplianceStatus.NON_COMPLIANT,
            severity=Severity.CRITICAL,
            rationale="Cap missing from contract section.",
        ),
        SectionCompareItem(
            section_id="s1",
            policy_document_id="550e8400-e29b-41d4-a716-446655440000",
            dimension_label="Liability",
            status=ComplianceStatus.NON_COMPLIANT,
            severity=Severity.CRITICAL,
            rationale="Duplicate should be dropped.",
        ),
    ]
    bundles = {
        "s1": SectionRetrievalBundle(section_id="s1", categories=["liability"], policy_hits=[]),
    }
    merged = merge_section_findings(items, bundles)
    assert len(merged.findings) == 1
    assert merged.findings[0].status == ComplianceStatus.NON_COMPLIANT
    assert any("deduped 1" in w for w in merged.warnings)


def test_merge_adds_no_policy_gap():
    bundles = {
        "s2": SectionRetrievalBundle(section_id="s2", categories=["privacy"], policy_hits=[]),
    }
    merged = merge_section_findings([], bundles)
    assert len(merged.findings) == 1
    assert merged.findings[0].status == ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT
    assert merged.findings[0].metadata.get("gap_type") == "no_policy"
    assert merged.gap_section_ids == ["s2"]
    assert merged.warnings


def test_merge_compare_transient_gap_type():
    items = [
        SectionCompareItem(
            section_id="s1",
            dimension_label="Liability",
            status=ComplianceStatus.INCONCLUSIVE,
            severity=Severity.INFO,
            rationale="Section compare failed: 429 rate limit",
        ),
    ]
    bundles = {
        "s1": SectionRetrievalBundle(section_id="s1", categories=["liability"], policy_hits=[]),
    }
    findings = section_items_to_findings(items)
    assert findings[0].metadata.get("gap_type") == "compare_transient"


def test_merge_compare_failed_gap_type_ipc():
    items = [
        SectionCompareItem(
            section_id="s1",
            dimension_label="Liability",
            status=ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
            severity=Severity.INFO,
            rationale="Section compare failed: no policy",
        ),
    ]
    findings = section_items_to_findings(items)
    assert findings[0].metadata.get("gap_type") == "compare_failed"


def test_merge_adds_compare_omitted_gap():
    from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk, RetrievalHit

    policy_hit = RetrievalHit(
        parent_chunk=IndexedChunk(
            chunk_id="p1",
            document_id=__import__("uuid").uuid4(),
            tenant_id="demo",
            kind=DocumentKind.POLICY,
            chunk_role=ChunkRole.PARENT,
            section_id="5",
            section_path="5",
            title="Indemnity",
            text="Vendor must indemnify.",
        ),
        score=0.9,
    )
    bundles = {
        "s3": SectionRetrievalBundle(
            section_id="s3",
            categories=["indemnity"],
            policy_hits=[policy_hit],
        ),
    }
    merged = merge_section_findings([], bundles)
    assert len(merged.findings) == 1
    assert merged.findings[0].metadata.get("gap_type") == "compare_omitted"
    assert merged.gap_section_ids == ["s3"]


def _contract_section(title: str, section_id: str, text: str = "x" * 50) -> IndexedChunk:
    return IndexedChunk(
        chunk_id=f"c-{section_id}",
        document_id=uuid4(),
        tenant_id="demo",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id=section_id,
        section_path=section_id,
        title=title,
        text=text,
    )


def test_merge_substantive_no_policy_inconclusive():
    bundles = {
        "s2": SectionRetrievalBundle(section_id="s2", categories=["privacy"], policy_hits=[]),
    }
    sections = {"s2": _contract_section("Data Protection", "s2")}
    merged = merge_section_findings([], bundles, sections_by_id=sections)
    assert merged.findings[0].status == ComplianceStatus.INCONCLUSIVE
    assert merged.findings[0].metadata.get("review_outcome") == "playbook_gap"
    assert merged.no_policy_gap_ids == ["s2"]


def test_merge_boilerplate_no_policy_insufficient():
    bundles = {
        "s9": SectionRetrievalBundle(section_id="s9", categories=["general"], policy_hits=[]),
    }
    sections = {"s9": _contract_section("Definitions", "s9", "Party means signatory.")}
    merged = merge_section_findings([], bundles, sections_by_id=sections)
    assert merged.findings[0].status == ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT
    assert merged.findings[0].metadata.get("review_outcome") == "boilerplate"


def test_merge_compare_omitted_inconclusive_with_sections():
    from document_core.schemas.chunk import RetrievalHit

    policy_hit = RetrievalHit(
        parent_chunk=IndexedChunk(
            chunk_id="p1",
            document_id=uuid4(),
            tenant_id="demo",
            kind=DocumentKind.POLICY,
            chunk_role=ChunkRole.PARENT,
            section_id="5",
            section_path="5",
            title="Indemnity",
            text="Vendor must indemnify.",
        ),
        score=0.9,
    )
    bundles = {
        "s3": SectionRetrievalBundle(
            section_id="s3",
            categories=["indemnity"],
            policy_hits=[policy_hit],
        ),
    }
    sections = {"s3": _contract_section("Indemnification", "s3", "Vendor indemnifies customer.")}
    merged = merge_section_findings([], bundles, sections_by_id=sections)
    assert merged.findings[0].status == ComplianceStatus.INCONCLUSIVE
    assert merged.findings[0].metadata.get("review_outcome") == "pipeline_incomplete"
    assert merged.compare_omitted_gap_ids == ["s3"]


def test_section_items_playbook_metadata():
    items = [
        SectionCompareItem(
            section_id="s1",
            policy_document_id="550e8400-e29b-41d4-a716-446655440000",
            dimension_label="Liability",
            status=ComplianceStatus.NON_COMPLIANT,
            severity=Severity.CRITICAL,
            rationale="Cap missing.",
            confidence=0.9,
        )
    ]
    hints = {
        "550e8400-e29b-41d4-a716-446655440000": PlaybookHints(
            policy_ref="vendor-liability",
            review_guidance="Require 12 month cap.",
        )
    }
    findings = section_items_to_findings(items, hints_by_document=hints)
    assert findings[0].metadata.get("source") == "playbook_compare"
    assert findings[0].metadata.get("policy_ref") == "vendor-liability"
    assert findings[0].metadata.get("playbook_guidance_used") is True


def test_section_items_tags_quote_validate_downgrade_metadata():
    items = [
        SectionCompareItem(
            section_id="5.2",
            policy_document_id="550e8400-e29b-41d4-a716-446655440000",
            dimension_label="Human rights",
            status=ComplianceStatus.INCONCLUSIVE,
            severity=Severity.IMPORTANT,
            rationale=(
                "Section aligns with policy. "
                "(Downgraded: model quotes were not exact substrings of the provided sections.)"
            ),
            confidence=0.8,
        )
    ]
    findings = section_items_to_findings(items)
    assert findings[0].metadata.get("downgrade_source") == "quote_validate"


def test_merge_tags_unclear_recompare_eligibility():
    items = [
        SectionCompareItem(
            section_id="s1",
            policy_document_id="550e8400-e29b-41d4-a716-446655440000",
            policy_section_id="5",
            dimension_label="Liability",
            status=ComplianceStatus.INCONCLUSIVE,
            severity=Severity.INFO,
            policy_quote="Cap must be 12 months.",
            rationale="Uncertain cap language.",
            confidence=0.3,
        ),
        SectionCompareItem(
            section_id="s2",
            policy_document_id="550e8400-e29b-41d4-a716-446655440000",
            dimension_label="Indemnity",
            status=ComplianceStatus.INCONCLUSIVE,
            severity=Severity.INFO,
            rationale="Section compare failed: timeout",
            confidence=0.1,
        ),
    ]
    bundles = {
        "s1": SectionRetrievalBundle(section_id="s1", categories=["liability"], policy_hits=[]),
        "s2": SectionRetrievalBundle(section_id="s2", categories=["indemnity"], policy_hits=[]),
    }
    merged = merge_section_findings(items, bundles)
    assert len(merged.unclear_finding_ids) == 2
    assert len(merged.unclear_recompare_finding_ids) == 2
    low_conf = next(
        f for f in merged.findings if f.metadata.get("unclear_reason") == "low_confidence"
    )
    assert low_conf.metadata.get("unclear_recompare_eligible") is True
    compare_failed = next(
        f for f in merged.findings if f.metadata.get("unclear_reason") == "compare_failed"
    )
    assert compare_failed.metadata.get("unclear_recompare_eligible") is True
    assert not any("not eligible for re-compare" in w for w in merged.warnings)


def test_merge_caps_findings_per_section():
    items = [
        SectionCompareItem(
            section_id="s1",
            policy_document_id="550e8400-e29b-41d4-a716-446655440000",
            dimension_label=f"Liability {idx}",
            status=ComplianceStatus.NON_COMPLIANT,
            severity=Severity.IMPORTANT,
            contract_quote=f"Distinct liability gap text number {idx} in contract.",
            policy_quote="Policy requires twelve month cap.",
            rationale=f"Gap {idx} is below policy minimum.",
        )
        for idx in range(4)
    ] + [
        SectionCompareItem(
            section_id="s1",
            policy_document_id="550e8400-e29b-41d4-a716-446655440000",
            dimension_label=f"Compliant {idx}",
            status=ComplianceStatus.COMPLIANT,
            severity=Severity.INFO,
            contract_quote=f"Compliant note text number {idx} in contract section.",
            policy_quote="Policy requires twelve month cap.",
            rationale="Contract meets this policy requirement adequately.",
        )
        for idx in range(2)
    ]
    bundles = {
        "s1": SectionRetrievalBundle(section_id="s1", categories=["liability"], policy_hits=[]),
    }
    merged = merge_section_findings(
        items,
        bundles,
        settings=ReviewSettings(section_compare_max_findings_per_section=4),
    )
    section_findings = [f for f in merged.findings if f.contract_section_id == "s1"]
    assert len(section_findings) == 4
    assert all(f.status == ComplianceStatus.NON_COMPLIANT for f in section_findings)
    assert any("capped" in w for w in merged.warnings)
