"""Tests for RC-04 battery tenant isolation (PF-1C)."""

from __future__ import annotations

import json
from pathlib import Path

from review_scope import policy_document_ids_from_sync

ROOT = Path(__file__).resolve().parents[1]


def test_atlassian_fixture_uses_dedicated_tenant():
    data = json.loads((ROOT / "fixtures" / "atlassian_e2e.json").read_text(encoding="utf-8"))
    assert data["tenant_id"] == "atlassian-demo"
    assert len(data["policies"]) == 9


def test_xecurify_fixture_uses_dedicated_tenant():
    data = json.loads((ROOT / "fixtures" / "xecurify_e2e.json").read_text(encoding="utf-8"))
    assert data["tenant_id"] == "xecurify-demo"


def test_policy_document_ids_from_sync():
    sync = {
        "policies": [
            {"document_id": "00000000-0000-4000-8000-000000000001"},
            {"document_id": "00000000-0000-4000-8000-000000000002"},
        ]
    }
    assert policy_document_ids_from_sync(sync) == [
        "00000000-0000-4000-8000-000000000001",
        "00000000-0000-4000-8000-000000000002",
    ]
