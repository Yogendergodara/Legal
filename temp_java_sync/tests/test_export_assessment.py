"""Tests for assessment slug and export helpers (Phase O)."""

from __future__ import annotations

import json

from export_assessment import assessment_slug
from e2e_harness import _sync_path_for_review


def test_assessment_slug_xecurify_and_acme() -> None:
    assert assessment_slug("Mutual NDA - Xecurify / Recipient") == "xecurify_nda"
    assert assessment_slug("Mutual NDA — Acme Corp / CloudVendor Inc.") == "acme_nda"


def test_sync_path_for_review_prefers_tenant_paired_file(tmp_path, monkeypatch) -> None:
    import export_assessment as export_mod
    import e2e_harness as harness_mod

    monkeypatch.setattr(export_mod, "OUTPUTS", tmp_path)
    monkeypatch.setattr(harness_mod, "OUTPUTS", tmp_path)

    review_path = tmp_path / "review_result.json"
    review_path.write_text(
        json.dumps({"tenant_id": "acme-nda-clean", "findings": []}),
        encoding="utf-8",
    )
    (tmp_path / "sync_acme-nda-clean.json").write_text(
        json.dumps({"tenant_id": "acme-nda-clean"}),
        encoding="utf-8",
    )
    (tmp_path / "sync_result.json").write_text(
        json.dumps({"tenant_id": "e2e-demo"}),
        encoding="utf-8",
    )

    chosen = _sync_path_for_review(review_path)
    assert chosen is not None
    assert chosen.name == "sync_acme-nda-clean.json"
