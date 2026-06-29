"""Direct document-mcp sync for Dev UI (Phase 36 — no normalization service)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from document_core.schemas.chunk import DocumentKind, IngestRequest, IngestSectionInput, ListSectionsRequest
from document_core.schemas.policy_sync import SyncPoliciesRequest
from document_core.schemas.registry import RegisterContractRequest
from document_core.services.policy_sync import (
    policy_sync_input_from_dict,
    sections_to_raw_text,
    slug_policy_ref as _slug_ref,
)
from document_core.services.registry import stable_contract_document_id, stable_policy_document_id
from review_agent.clients.document_client import DocumentMCPClient

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"
POLICY_FIXTURES = FIXTURES / "policies"
OUTPUTS = ROOT / "outputs"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_tenant(*, shared: bool, configured: str) -> str:
    if shared:
        return configured or "e2e-demo"
    return f"dev-ui-{uuid.uuid4().hex[:8]}"


async def tombstone_tenant_policies(client: DocumentMCPClient, tenant_id: str) -> list[str]:
    """Delete all indexed policies for tenant (dev-only shared-tenant reset)."""
    refs: list[str] = []
    registry = await client.list_policy_registry(tenant_id=tenant_id, kind="policy")
    for entry in registry.policies:
        ref = entry.policy_ref
        if not ref:
            continue
        await client.delete_policy(tenant_id, ref)
        refs.append(ref)
    return refs


def _policy_result(
    *,
    policy_ref: str,
    document_id: str,
    ingest_result: Any,
    categories: list[str],
) -> dict[str, Any]:
    return {
        "kind": "policy",
        "policy_ref": policy_ref,
        "document_id": str(document_id),
        "index_status_after": "indexed",
        "parent_count": ingest_result.parent_count,
        "structure_confidence": str(ingest_result.structure_confidence.value),
        "categories": categories,
        "warnings": list(ingest_result.warnings or []),
        "skipped": False,
    }


def _contract_result(
    *,
    contract_ref: str,
    document_id: str,
    ingest_result: Any,
    section_ids: list[str],
) -> dict[str, Any]:
    return {
        "kind": "contract",
        "contract_ref": contract_ref,
        "document_id": str(document_id),
        "index_status_after": "indexed",
        "parent_count": ingest_result.parent_count,
        "structure_confidence": str(ingest_result.structure_confidence.value),
        "warnings": list(ingest_result.warnings or []),
        "skipped": False,
        "section_count": len(section_ids),
        "section_ids": section_ids,
    }


async def _ingest_contract_structured(
    client: DocumentMCPClient,
    *,
    tenant_id: str,
    contract_data: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    contract_ref = contract_data["contract_ref"]
    document_id = stable_contract_document_id(tenant_id, contract_ref)
    await client.register_contract(
        RegisterContractRequest(
            tenant_id=tenant_id,
            contract_ref=contract_ref,
            title=contract_data["title"],
            document_id=document_id,
            contract_type=contract_data.get("contract_type", "nda"),
        )
    )
    sections = [
        IngestSectionInput(
            section_id=str(s["section_id"]),
            title=str(s.get("title") or ""),
            text=str(s["text"]),
        )
        for s in contract_data["sections"]
    ]
    result = await client.ingest_document(
        IngestRequest(
            tenant_id=tenant_id,
            document_id=document_id,
            title=contract_data["title"],
            kind=DocumentKind.CONTRACT,
            sections=sections,
            metadata=dict(contract_data.get("metadata") or {}),
        )
    )
    section_ids = [s.section_id for s in sections]
    return (
        _contract_result(
            contract_ref=contract_ref,
            document_id=str(document_id),
            ingest_result=result,
            section_ids=section_ids,
        ),
        section_ids,
    )


async def _ingest_policy_structured(
    client: DocumentMCPClient,
    *,
    tenant_id: str,
    policy_data: dict[str, Any],
    policy_ref: str | None = None,
) -> dict[str, Any]:
    payload = dict(policy_data)
    if not payload.get("document_id"):
        ref = policy_ref or payload.get("policy_ref")
        if ref:
            payload["document_id"] = str(stable_policy_document_id(tenant_id, ref))
    payload.pop("policy_ref", None)
    source = str((policy_data.get("metadata") or {}).get("source") or "dev-ui-policies")
    response = await client.sync_policies(
        SyncPoliciesRequest(
            tenant_id=tenant_id,
            policies=[policy_sync_input_from_dict(payload)],
            replace_policies=False,
            source=source,
        )
    )
    return response.policies[0].model_dump(mode="json")


async def sync_policies_only(
    client: DocumentMCPClient,
    *,
    tenant_id: str,
    policies: list[dict[str, Any]],
    replace_policies: bool = False,
) -> dict[str, Any]:
    """Index tenant playbooks via document-mcp /tools/sync_policies."""
    response = await client.sync_policies(
        SyncPoliciesRequest(
            tenant_id=tenant_id,
            policies=[policy_sync_input_from_dict(p) for p in policies],
            replace_policies=replace_policies,
            source="dev-ui-policies",
        )
    )
    return response.model_dump(mode="json")


async def sync_fixture_policies(
    client: DocumentMCPClient,
    *,
    tenant_id: str,
    replace_policies: bool = False,
) -> dict[str, Any]:
    """Load default NDA policy fixtures into the tenant index."""
    policies_payload: list[dict[str, Any]] = []
    for path in sorted(POLICY_FIXTURES.glob("*.json")):
        policy_data = _load_json(path)
        policies_payload.append(
            {
                "title": policy_data.get("title") or path.stem,
                "policy_type": policy_data.get("policy_type") or "nda",
                "review_guidance": (policy_data.get("metadata") or {}).get("review_guidance") or "",
                "text": policy_data.get("text")
                or sections_to_raw_text(list(policy_data.get("sections") or [])),
            }
        )
    return await sync_policies_only(
        client,
        tenant_id=tenant_id,
        policies=policies_payload,
        replace_policies=replace_policies,
    )


def fixture_contract_raw_text() -> str:
    contract_data = _load_json(FIXTURES / "nda_contract.json")
    text = str(contract_data.get("text") or "").strip()
    if text:
        return text
    return sections_to_raw_text(list(contract_data.get("sections") or []))


async def sync_fixture_bundle(
    client: DocumentMCPClient,
    *,
    tenant_id: str,
    replace_policies: bool = False,
) -> dict[str, Any]:
    tombstoned: list[str] = []
    if replace_policies:
        tombstoned = await tombstone_tenant_policies(client, tenant_id)

    contract_path = FIXTURES / "nda_contract.json"
    contract_data = _load_json(contract_path)
    contract_data["tenant_id"] = tenant_id

    contract, section_ids = await _ingest_contract_structured(
        client, tenant_id=tenant_id, contract_data=contract_data
    )

    policies: list[dict[str, Any]] = []
    for path in sorted(POLICY_FIXTURES.glob("*.json")):
        policy_data = _load_json(path)
        policy_data["tenant_id"] = tenant_id
        policies.append(
            await _ingest_policy_structured(client, tenant_id=tenant_id, policy_data=policy_data)
        )

    return _build_sync_payload(
        tenant_id=tenant_id,
        contract=contract,
        policies=policies,
        section_ids=section_ids,
        tombstoned=tombstoned,
    )


async def sync_custom_payload(
    client: DocumentMCPClient,
    *,
    tenant_id: str,
    payload: dict[str, Any],
    replace_policies: bool = False,
) -> dict[str, Any]:
    tombstoned: list[str] = []
    if replace_policies:
        tombstoned = await tombstone_tenant_policies(client, tenant_id)

    contract_block = payload["contract"]
    contract_ref = _slug_ref("custom-contract", contract_block.get("title", "contract"))
    contract_data = {
        "tenant_id": tenant_id,
        "contract_ref": contract_ref,
        "title": contract_block.get("title") or "Custom Contract",
        "contract_type": contract_block.get("contract_type") or "nda",
        "metadata": {"source": "dev-ui-custom"},
        "sections": contract_block.get("sections") or [],
    }
    contract, section_ids = await _ingest_contract_structured(
        client, tenant_id=tenant_id, contract_data=contract_data
    )

    policies: list[dict[str, Any]] = []
    for index, policy in enumerate(payload.get("policies") or [], start=1):
        if not (policy.get("text") or "").strip() and not policy.get("sections"):
            continue
        ref = _slug_ref(f"playbook-{index}", policy.get("title") or f"policy-{index}")
        policy_data = {
            "tenant_id": tenant_id,
            "policy_ref": ref,
            "title": policy.get("title") or f"Policy {index}",
            "policy_type": policy.get("policy_type") or contract_data["contract_type"],
            "categories": list(policy.get("categories") or []),
            "metadata": {
                "source": "dev-ui-custom",
                "categories": list(policy.get("categories") or []),
                "review_guidance": policy.get("review_guidance") or "",
            },
            "text": policy.get("text") or "",
        }
        policies.append(
            await _ingest_policy_structured(
                client,
                tenant_id=tenant_id,
                policy_data=policy_data,
                policy_ref=ref,
            )
        )

    return _build_sync_payload(
        tenant_id=tenant_id,
        contract=contract,
        policies=policies,
        section_ids=section_ids,
        tombstoned=tombstoned,
    )


async def sync_upload_bundle(
    client: DocumentMCPClient,
    *,
    tenant_id: str,
    contract_title: str,
    contract_type: str,
    contract_text: str,
    policy_uploads: list[tuple[str, str, list[str]]],
    replace_policies: bool = False,
) -> dict[str, Any]:
    """policy_uploads: (filename, text, categories)"""
    tombstoned: list[str] = []
    if replace_policies:
        tombstoned = await tombstone_tenant_policies(client, tenant_id)

    contract_ref = _slug_ref("upload-contract", contract_title)
    document_id = stable_contract_document_id(tenant_id, contract_ref)
    await client.register_contract(
        RegisterContractRequest(
            tenant_id=tenant_id,
            contract_ref=contract_ref,
            title=contract_title,
            document_id=document_id,
            contract_type=contract_type,
        )
    )
    result = await client.ingest_document(
        IngestRequest(
            tenant_id=tenant_id,
            document_id=document_id,
            title=contract_title,
            kind=DocumentKind.CONTRACT,
            text=contract_text,
            metadata={"source": "dev-ui-upload", "contract_ref": contract_ref, "contract_type": contract_type},
        )
    )
    sections = await client.list_sections(
        ListSectionsRequest(
            tenant_id=tenant_id,
            document_id=document_id,
            kind=DocumentKind.CONTRACT,
        )
    )
    section_ids = [s.section_id for s in sections]
    contract = _contract_result(
        contract_ref=contract_ref,
        document_id=str(document_id),
        ingest_result=result,
        section_ids=section_ids,
    )

    policies: list[dict[str, Any]] = []
    upload_sources: list[dict[str, Any]] = []
    for filename, text, categories in policy_uploads:
        title = title_from_upload_filename(filename)
        ref = _slug_ref("upload-policy", title)
        policy_data = {
            "tenant_id": tenant_id,
            "policy_ref": ref,
            "title": title,
            "policy_type": contract_type,
            "categories": categories,
            "metadata": {
                "source": "dev-ui-upload",
                "categories": categories,
                "filename": filename,
            },
            "text": text,
        }
        policies.append(
            await _ingest_policy_structured(
                client, tenant_id=tenant_id, policy_data=policy_data, policy_ref=ref
            )
        )
        upload_sources.append(
            {
                "filename": filename,
                "title": title,
                "categories": categories,
                "inferred": True,
            }
        )

    payload = _build_sync_payload(
        tenant_id=tenant_id,
        contract=contract,
        policies=policies,
        section_ids=section_ids,
        tombstoned=tombstoned,
    )
    payload["upload_sources"] = {
        "contract_title": contract_title,
        "contract_type": contract_type,
        "policy_metadata": upload_sources,
    }
    return payload


def title_from_upload_filename(filename: str) -> str:
    from upload_text import title_from_filename

    return title_from_filename(filename)


def _build_sync_payload(
    *,
    tenant_id: str,
    contract: dict[str, Any],
    policies: list[dict[str, Any]],
    section_ids: list[str],
    tombstoned: list[str],
) -> dict[str, Any]:
    primaries = [p.get("categories", [""])[0] for p in policies if p.get("categories")]
    dupes = sorted({c for c in primaries if primaries.count(c) > 1})
    weak_tag_policies = [
        p.get("title") or p.get("policy_ref")
        for p in policies
        if any(
            "weak_tags" in w or "tagger=keyword" in w or w.startswith("unexpected_tags:")
            for w in (p.get("warnings") or [])
        )
    ]
    return {
        "tenant_id": tenant_id,
        "contract": contract,
        "contract_document_id": contract["document_id"],
        "policies": policies,
        "verify": {
            "document_id": contract["document_id"],
            "section_count": len(section_ids),
            "section_ids": section_ids,
        },
        "tombstoned_policy_refs": tombstoned,
        "preflight": {
            "policies_synced": len(policies),
            "tombstoned_count": len(tombstoned),
            "duplicate_primary_categories": dupes,
            "weak_tag_count": len(weak_tag_policies),
            "weak_tag_policies": weak_tag_policies,
        },
    }


def save_sync_result(sync: dict[str, Any]) -> Path:
    OUTPUTS.mkdir(exist_ok=True)
    payload = json.dumps(sync, indent=2)
    path = OUTPUTS / "sync_result.json"
    path.write_text(payload, encoding="utf-8")
    tenant = str(sync.get("tenant_id") or "").strip()
    if tenant:
        (OUTPUTS / f"sync_{tenant}.json").write_text(payload, encoding="utf-8")
    return path
