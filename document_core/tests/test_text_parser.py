"""Tests for raw-text section parsing (Phase 37B)."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from document_core.parser.text_parser import normalize_extracted_text, parse_text_to_tree
from document_core.schemas.chunk import IngestRequest, StructureConfidence
from tests.fixtures import SAMPLE_CONTRACT, SAMPLE_POLICY

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
ACME_SECTIONS = json.loads((_FIXTURES / "acme_nda_sections.json").read_text(encoding="utf-8"))


def _flatten_sections(sections: list[dict]) -> str:
    return "\n\n".join(
        f"{section['section_id']}. {section['title']}\n{section['text']}" for section in sections
    )


def _walk_sections(nodes):
    for node in nodes:
        yield node
        yield from _walk_sections(node.children)


def test_normalize_strips_page_footers_and_dehyphenates():
    raw = "3. Confidential Information\nliabil-\nity cap.\n\nPage 2 of 10\n\n4. Term\nOne year."
    tree = parse_text_to_tree(document_id=uuid4(), title="T", text=raw)
    assert len(tree.sections) >= 2
    first_text = tree.sections[0].text.replace("\n", " ")
    assert "liability" in first_text


def test_parse_acme_nda_raw_text():
    raw = _flatten_sections(ACME_SECTIONS)
    tree = parse_text_to_tree(document_id=uuid4(), title="NDA", text=raw)
    ids = {node.section_id for node in tree.sections}
    assert len(tree.sections) >= 8
    assert "3" in ids
    assert "6" in ids
    assert "7" in ids
    assert tree.structure_confidence == StructureConfidence.HIGH
    liability = next(node for node in tree.sections if node.section_id == "6")
    assert "Limitation of Liability" in liability.title
    assert "fees paid" in liability.text.lower() or "twelve (12) months" in liability.text.lower()


def test_parse_msa_numbered_sections():
    tree = parse_text_to_tree(document_id=uuid4(), title="MSA", text=SAMPLE_CONTRACT)
    all_nodes = list(_walk_sections(tree.sections))
    ids = {node.section_id for node in all_nodes}
    titles = " ".join(node.title.lower() for node in all_nodes)
    assert "12.2" in ids or any("12.2" in node.section_id for node in all_nodes)
    assert "indemnif" in titles
    assert tree.structure_confidence in {
        StructureConfidence.MEDIUM,
        StructureConfidence.HIGH,
    }


def test_parse_policy_playbook_sections():
    tree = parse_text_to_tree(document_id=uuid4(), title="Policy", text=SAMPLE_POLICY)
    assert len(tree.sections) >= 2
    titles = " ".join(node.title.lower() for node in tree.sections)
    assert "liability" in titles
    assert "indemnif" in titles


def test_ingest_rejects_whitespace_only_text():
    with pytest.raises(ValueError, match="text is required"):
        IngestRequest(tenant_id="t1", title="Doc", text="   \n\t  ")


def test_low_confidence_blob_text():
    blob = "This is a single paragraph with no headings. " * 80
    tree = parse_text_to_tree(document_id=uuid4(), title="Blob", text=blob)
    assert tree.structure_confidence == StructureConfidence.LOW
    assert len(tree.sections) == 1


def test_incident_response_severity_lines_do_not_duplicate_section_ids():
    """Lettered severity lines like 'c. Low Severity (Level 3):' must not collide with '3. Roles'."""
    raw = """3. Roles and Responsibilities
The ISO team oversees this policy.

4. Policy
Severity levels:
a. Highest Severity (Level 1): environmental threat.
b. Medium Severity (Level 2): third-party downtime.
c. Low Severity (Level 3): internal network incidents.
"""
    tree = parse_text_to_tree(document_id=uuid4(), title="Incident Response", text=raw)
    all_nodes = list(_walk_sections(tree.sections))
    ids = [node.section_id for node in all_nodes]
    assert len(ids) == len(set(ids)), f"duplicate section_ids: {ids}"
    assert "3" in ids
    assert not any(node.title.startswith("c. Low Severity") for node in all_nodes)


def test_numbered_exclusion_prose_not_parsed_as_section_heading():
    raw = """1. Definitions
Confidential Information means all non-public information.

1.2 Exclusions
The obligations do not apply to information that:
1. Is or becomes publicly available through no act or omission of the Receiving Party;
2. Was known to the Receiving Party prior to disclosure;
"""
    tree = parse_text_to_tree(document_id=uuid4(), title="NDA", text=raw)
    all_nodes = list(_walk_sections(tree.sections))
    titles = [node.title for node in all_nodes]
    assert not any(t.startswith("1. Is or becomes") for t in titles)
    assert any("Definitions" in t for t in titles)
