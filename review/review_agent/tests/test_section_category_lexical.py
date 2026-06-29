"""P1-4: Lexical category inference when section classifier LLM fails."""

from __future__ import annotations

from uuid import UUID

import pytest

from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk
from document_core.schemas.taxonomy import normalize_categories
from review_agent.services.section_category_lexical import (
    infer_categories_from_section,
    infer_lexical_classify,
    infer_query_terms_from_lexical,
)


def _section(title: str, text: str = "", section_id: str = "1") -> IndexedChunk:
    return IndexedChunk(
        chunk_id=f"c-{section_id}",
        document_id=UUID("00000000-0000-0000-0000-000000000001"),
        tenant_id="demo",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id=section_id,
        section_path=section_id,
        title=title,
        text=text or title,
    )


def test_infer_liability_from_title() -> None:
    section = _section("Limitation of Liability", "Cap shall not exceed fees paid.")
    assert "liability" in infer_categories_from_section(section)


def test_infer_indemnity_from_indemnification_title() -> None:
    section = _section("Indemnification", "Vendor shall indemnify Customer.")
    assert "indemnity" in infer_categories_from_section(section)


def test_infer_confidentiality_from_title() -> None:
    section = _section("Confidential Information", "Receiving party shall protect data.")
    assert "confidentiality" in infer_categories_from_section(section)


def test_empty_section_still_empty() -> None:
    section = _section("Definitions", "Party means a signatory to this Agreement.")
    assert infer_categories_from_section(section) == []


def test_category_alias_indemnification() -> None:
    assert normalize_categories(["indemnification"]) == ["indemnity"]


_CISCO_S2_TEXT = (
    "Supplier represents that it complies with local labor laws. Supplier may use "
    "recruitment agencies and may pass reasonable recruitment, placement, and processing "
    "fees to workers where permitted by local law."
)
_CISCO_S3_TEXT = (
    "Supplier will use commercially reasonable efforts to source materials responsibly. "
    "Supplier is not obligated to complete Minerals Reporting Templates (MRTs), identify "
    "smelters or refiners in its supply chain."
)
_CISCO_S4_TEXT = (
    "Supplier shall comply with applicable environmental laws. Supplier is not required "
    "to report greenhouse gas emissions to CDP or any other registry."
)


def test_infer_cisco_supplier_code_of_conduct() -> None:
    section = _section(
        "Supplier Code of Conduct",
        "Supplier shall use commercially reasonable efforts to comply with applicable local laws.",
        section_id="1",
    )
    assert "compliance" in infer_categories_from_section(section)


def test_infer_cisco_human_rights_and_labor() -> None:
    section = _section("Human Rights and Labor", _CISCO_S2_TEXT, section_id="2")
    cats = infer_categories_from_section(section)
    assert "human_rights" in cats
    assert "labor" in cats


def test_infer_cisco_responsible_minerals() -> None:
    section = _section("Responsible Minerals", _CISCO_S3_TEXT, section_id="3")
    assert "minerals" in infer_categories_from_section(section)


def test_infer_cisco_environment_and_ghg() -> None:
    section = _section("Environment and GHG Emissions", _CISCO_S4_TEXT, section_id="4")
    cats = infer_categories_from_section(section)
    assert "environment" in cats


def test_lexical_confidence_title_hr() -> None:
    section = _section("Human Rights and Labor", _CISCO_S2_TEXT, section_id="2")
    result = infer_lexical_classify(section)
    assert result.confidence == "title"
    assert "human_rights" in result.categories


def test_lexical_confidence_none_definitions() -> None:
    section = _section("Definitions", "Party means a signatory to this Agreement.")
    result = infer_lexical_classify(section)
    assert result.confidence == "none"
    assert result.categories == []


def test_query_terms_liability_policy_phrase() -> None:
    section = _section("Limitation of Liability", "Cap shall not exceed fees paid.")
    terms = infer_query_terms_from_lexical(["liability"], section)
    assert "limitation of liability" in terms[0].lower()


def test_query_terms_minerals_policy_phrase() -> None:
    section = _section("Responsible Minerals", _CISCO_S3_TEXT, section_id="3")
    terms = infer_query_terms_from_lexical(["minerals"], section)
    joined = " ".join(terms).lower()
    assert "mrt" in joined or "minerals" in joined


def test_infer_categories_backward_compat() -> None:
    section = _section("Indemnification", "Vendor shall indemnify Customer.")
    assert infer_categories_from_section(section) == infer_lexical_classify(section).categories


def test_lexical_body_beyond_200_chars() -> None:
    prefix = "x" * 400
    section = _section(
        "Miscellaneous",
        prefix + " The total limitation of liability shall not exceed fees paid.",
    )
    result = infer_lexical_classify(section)
    assert "liability" in result.categories
    assert result.confidence == "body"


def test_lexical_full_body_short_section() -> None:
    body = ("intro " * 50) + "Vendor shall indemnify Customer for third-party claims."
    section = _section("Obligations", body)
    result = infer_lexical_classify(section, full_body_max_chars=4000)
    assert "indemnity" in result.categories


def test_body_compliance_dropped_without_code_of_conduct() -> None:
    section = _section(
        "5",
        "Recipient shall comply with export control and anti-corruption laws of all jurisdictions.",
    )
    result = infer_lexical_classify(section)
    assert "compliance" not in result.categories


def test_body_compliance_kept_with_code_of_conduct() -> None:
    section = _section(
        "Code of Conduct",
        "Recipient shall comply with the supplier code of conduct and related standards.",
    )
    result = infer_lexical_classify(section)
    assert "compliance" in result.categories
