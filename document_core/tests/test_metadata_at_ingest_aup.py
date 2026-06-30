"""AUP / acceptable-use keyword category inference (OB-02B)."""

from __future__ import annotations

from document_core.services.metadata_at_ingest import infer_section_categories_keyword


def test_aup_dmca_maps_to_ip() -> None:
    cats = infer_section_categories_keyword(
        title="Prohibited Activities",
        text="You may not circumvent copyright or file false DMCA notices.",
    )
    assert "ip" in cats


def test_aup_malware_maps_to_access_control() -> None:
    cats = infer_section_categories_keyword(
        title="Security",
        text="Do not distribute malware or engage in hacking.",
    )
    assert "access_control" in cats


def test_aup_unauthorized_access() -> None:
    cats = infer_section_categories_keyword(
        title="Account misuse",
        text="Unauthorized access to another user's account is prohibited.",
    )
    assert "access_control" in cats


def test_aup_ai_misuse() -> None:
    cats = infer_section_categories_keyword(
        title="AI usage",
        text="Misuse of generative AI features to produce harmful content.",
    )
    assert "ai_usage" in cats


def test_aup_mixed_clause_multi_specific() -> None:
    cats = infer_section_categories_keyword(
        title="Prohibited Activities",
        text=(
            "Prohibited use includes malware, copyright infringement under DMCA, "
            "and misuse of machine learning systems."
        ),
    )
    assert "access_control" in cats
    assert "ip" in cats
    assert "ai_usage" in cats
