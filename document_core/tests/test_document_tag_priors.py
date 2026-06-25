"""Tests for document-level tag priors (Phase D)."""

from __future__ import annotations

from document_core.services.document_tag_priors import (
    apply_document_priors,
    assess_policy_tag_quality,
    document_prior_hint,
)


def test_coc_prior_suppresses_sla_and_prefers_human_rights() -> None:
    result = apply_document_priors(
        ["sla", "employment", "compliance"],
        document_title="CODE OF CONDUCT",
    )
    assert "sla" not in result
    assert "employment" not in result
    assert "human_rights" in result


def test_logo_prior_suppresses_security() -> None:
    result = apply_document_priors(
        ["security", "general"],
        document_title="Logo/Trademark Usage Guidelines",
    )
    assert "security" not in result
    assert "trademark" in result
    assert "ip" in result


def test_document_prior_hint_for_incident_response() -> None:
    hint = document_prior_hint("Incident Response Plan")
    assert "incident_reporting" in hint
    assert "sla" in hint


def test_assess_warns_on_keyword_tagger() -> None:
    warnings = assess_policy_tag_quality(
        document_title="Privacy Policy",
        section_categories=[["privacy"]],
        tagger="keyword",
        document_union=["privacy"],
    )
    assert any("tagger=keyword" in w for w in warnings)


def test_assess_warns_on_unexpected_suppressed_tags() -> None:
    warnings = assess_policy_tag_quality(
        document_title="Logo/Trademark Usage Guidelines",
        section_categories=[["security"]],
        tagger="llm",
        document_union=["security", "general"],
    )
    assert any(w.startswith("unexpected_tags:") for w in warnings)
