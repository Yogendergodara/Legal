"""Tests for batch policy sync (Java ingest path)."""

from __future__ import annotations

from uuid import UUID

import pytest

from document_core.schemas.policy_sync import PolicySyncInput, SyncPoliciesRequest
from document_core.services.policy_sync import (
    policy_sync_input_from_dict,
    sections_to_raw_text,
    slug_policy_ref,
    sync_policies,
)
from document_core.services.registry import get_policy_by_ref
from document_core.services.search import list_sections
from document_core.schemas.chunk import DocumentKind, ListSectionsRequest


def test_sections_to_raw_text_joins_titles() -> None:
    raw = sections_to_raw_text(
        [
            {"title": "Term", "text": "Three years."},
            {"title": "Liability", "text": "Cap is $100k."},
        ]
    )
    assert "Term" in raw
    assert "Three years." in raw
    assert "Liability" in raw


def test_slug_policy_ref() -> None:
    assert slug_policy_ref("playbook-1", "Data Retention Policy!").startswith("playbook-1-data-retention")


def test_policy_sync_input_from_dict_flattens_sections() -> None:
    parsed = policy_sync_input_from_dict(
        {
            "title": "Confidentiality",
            "document_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "policy_ref": "atlassian-privacy-policy",
            "sections": [{"section_id": "1", "title": "Scope", "text": "All secrets."}],
        }
    )
    assert parsed.document_id == UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    assert "All secrets." in parsed.text
    assert parsed.policy_ref == "atlassian-privacy-policy"


@pytest.mark.asyncio
async def test_sync_policies_preserves_explicit_policy_ref(store) -> None:
    request = SyncPoliciesRequest(
        tenant_id="atlassian-tenant",
        policies=[
            PolicySyncInput(
                policy_ref="atlassian-privacy-policy",
                title="Atlassian Privacy Policy",
                text="We process personal data and honor data subject rights.",
            )
        ],
    )
    response = await sync_policies(request, store=store)
    assert response.policies[0].policy_ref == "atlassian-privacy-policy"
    record = get_policy_by_ref("atlassian-tenant", "atlassian-privacy-policy", store=store)
    assert record is not None


@pytest.mark.asyncio
async def test_sync_policies_uses_java_document_id(store) -> None:
    java_doc_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    request = SyncPoliciesRequest(
        tenant_id="java-tenant",
        policies=[
            PolicySyncInput(
                document_id=java_doc_id,
                title="Custom ID Playbook",
                text="Vendor liability cap is one million dollars.",
            )
        ],
    )
    response = await sync_policies(request, store=store)
    assert response.policies[0].document_id == str(java_doc_id)

    record = get_policy_by_ref("java-tenant", str(java_doc_id), store=store)
    assert record is not None
    assert record.document_id == java_doc_id


@pytest.mark.asyncio
async def test_sync_policies_indexes_and_chunks(store) -> None:
    request = SyncPoliciesRequest(
        tenant_id="java-tenant",
        policies=[
            PolicySyncInput(
                title="Liability Cap Playbook",
                text="Liability shall not exceed one million dollars.",
            )
        ],
        replace_policies=False,
        source="java-sync",
    )
    response = await sync_policies(request, store=store)
    assert response.tenant_id == "java-tenant"
    assert len(response.policies) == 1
    assert response.policies[0].policy_ref.startswith("playbook-1-liability")
    assert response.policies[0].parent_count >= 1

    record = get_policy_by_ref("java-tenant", response.policies[0].policy_ref, store=store)
    assert record is not None
    assert record.index_status == "indexed"

    sections = await list_sections(
        ListSectionsRequest(
            tenant_id="java-tenant",
            document_id=record.document_id,
            kind=DocumentKind.POLICY,
        ),
        store=store,
    )
    assert sections
    assert "million" in sections[0].text.lower()
