"""Tests for named policy routing."""

from __future__ import annotations

from review_agent.services.named_policy_routing import (
    extract_named_policy_title_keys,
    resolve_named_policy_doc_ids,
)


def test_extract_security_practices_policy_reference():
    text = (
        "The Receiving Party shall implement security measures consistent with "
        "Xecurify's Security Practices Policy, including encryption."
    )
    keys = extract_named_policy_title_keys(text)
    assert any("security" in k for k in keys)


def test_resolve_named_policy_doc_ids_matches_title():
    catalog = [
        {"document_id": "sec-1", "title": "Security Practices"},
        {"document_id": "ret-1", "title": "Data Retention"},
    ]
    ids = resolve_named_policy_doc_ids(["security practice"], catalog)
    assert ids == ["sec-1"]
