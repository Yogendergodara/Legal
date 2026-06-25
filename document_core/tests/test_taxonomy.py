"""Tests for taxonomy expansion and cap (Phase D)."""

from __future__ import annotations

from document_core.schemas.taxonomy import (
    BROAD_POLICY_CATEGORIES,
    cap_section_categories,
    normalize_categories,
)


def test_normalize_gdpr_alias() -> None:
    assert normalize_categories(["gdpr"]) == ["data_subject_rights"]


def test_normalize_incident_response_alias() -> None:
    assert normalize_categories(["incident_response"]) == ["incident_reporting"]


def test_cap_drops_broad_when_specific_exists() -> None:
    capped = cap_section_categories(
        ["confidentiality", "compliance", "security", "privacy"],
        max_tags=3,
    )
    assert capped == ["confidentiality", "privacy"]
    assert "compliance" not in capped
    assert "security" not in capped


def test_cap_keeps_broad_when_no_specific() -> None:
    capped = cap_section_categories(["compliance", "general"], max_tags=2)
    assert capped == ["compliance", "general"]


def test_broad_categories_include_security() -> None:
    assert "security" in BROAD_POLICY_CATEGORIES
