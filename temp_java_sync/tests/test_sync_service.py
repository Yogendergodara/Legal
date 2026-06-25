"""Tests for direct document-mcp sync service."""

from __future__ import annotations

from sync_service import _build_sync_payload


def test_sections_to_raw_text_joins_titles() -> None:
    from sync_service import fixture_contract_raw_text, sections_to_raw_text

    raw = sections_to_raw_text(
        [
            {"title": "Term", "text": "Three years."},
            {"title": "Liability", "text": "Cap is $100k."},
        ]
    )
    assert "Term" in raw
    assert "Three years." in raw
    assert "Liability" in raw
    fixture = fixture_contract_raw_text()
    assert "Confidential Information" in fixture


def test_tombstone_uses_policies_field() -> None:
    from unittest.mock import AsyncMock

    from document_core.schemas.registry import ListPolicyRegistryResponse, PolicyRegistryRecord

    record = PolicyRegistryRecord(
        tenant_id="e2e-demo",
        document_id="a97062aa-052c-5976-8dfc-87f3f53bfba2",
        policy_ref="playbook-confidentiality-standard",
        title="Standard Confidentiality Playbook",
        index_status="indexed",
    )
    client = AsyncMock()
    client.list_policy_registry = AsyncMock(
        return_value=ListPolicyRegistryResponse(tenant_id="e2e-demo", policies=[record])
    )
    client.delete_policy = AsyncMock()

    import asyncio

    from sync_service import tombstone_tenant_policies

    refs = asyncio.run(tombstone_tenant_policies(client, "e2e-demo"))
    assert refs == ["playbook-confidentiality-standard"]
    client.delete_policy.assert_awaited_once_with("e2e-demo", "playbook-confidentiality-standard")


def test_build_sync_payload_shape() -> None:
    contract = {
        "document_id": "c1",
        "categories": [],
    }
    policies = [
        {"document_id": "p1", "categories": ["liability"]},
        {"document_id": "p2", "categories": ["indemnity"]},
    ]
    payload = _build_sync_payload(
        tenant_id="e2e-demo",
        contract=contract,
        policies=policies,
        section_ids=["1", "2"],
        tombstoned=["old-policy"],
    )
    assert payload["tenant_id"] == "e2e-demo"
    assert payload["contract_document_id"] == "c1"
    assert len(payload["policies"]) == 2
    assert payload["verify"]["section_count"] == 2
    assert payload["preflight"]["policies_synced"] == 2
    assert payload["preflight"]["tombstoned_count"] == 1


def test_platform_payload_includes_policy_ids() -> None:
    from review_output import build_platform_review_payload

    body = build_platform_review_payload(
        tenant_id="demo",
        contract_document_id="c1",
        policy_document_ids=["p1", "p2"],
        contract_title="NDA",
        contract_type="nda",
        policy_source="session",
    )
    assert body["policy_document_ids"] == ["p1", "p2"]
    assert body["policy_source"] == "session"

    indexed = build_platform_review_payload(
        tenant_id="demo",
        contract_text="Section 1. Liability cap.",
        contract_title="NDA",
        contract_type="nda",
    )
    assert indexed["contract_text"].startswith("Section")
    assert indexed["policy_source"] == "indexed"
    assert "policy_document_ids" not in indexed


def test_save_sync_result_writes_tenant_snapshot(tmp_path, monkeypatch) -> None:
    import json

    from sync_service import save_sync_result

    monkeypatch.setattr("sync_service.OUTPUTS", tmp_path)
    sync = {"tenant_id": "acme-nda-clean", "policies": [{"policy_ref": "p1"}]}
    save_sync_result(sync)
    assert (tmp_path / "sync_result.json").is_file()
    tenant_path = tmp_path / "sync_acme-nda-clean.json"
    assert tenant_path.is_file()
    assert json.loads(tenant_path.read_text(encoding="utf-8"))["tenant_id"] == "acme-nda-clean"


def test_build_assessment_uses_review_tenant_when_sync_missing() -> None:
    from export_assessment import build_assessment

    assessment = build_assessment(
        {"findings": [], "tenant_id": "acme-nda-clean", "discovered_policy_document_ids": ["p1"]},
        sync=None,
    )
    assert assessment["tenant_id"] == "acme-nda-clean"


def test_infer_categories_from_filename() -> None:
    from upload_text import infer_categories_from_filename, read_upload_text

    assert "liability" in infer_categories_from_filename("MSA_Liability_Playbook.pdf")
    assert infer_categories_from_filename("misc.txt") == ["general"]
    text = read_upload_text("contract.txt", b"Section 1\nHello world")
    assert "Hello world" in text
