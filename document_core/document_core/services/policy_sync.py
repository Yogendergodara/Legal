"""Batch policy sync — register, chunk, index (Java / external catalog ingest)."""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from document_core.schemas.chunk import DocumentKind, IngestRequest, ListSectionsRequest
from document_core.schemas.policy_sync import (
    PolicySyncInput,
    PolicySyncResult,
    SyncPoliciesPreflight,
    SyncPoliciesRequest,
    SyncPoliciesResponse,
)
from document_core.schemas.registry import (
    DeletePolicyRequest,
    ListPolicyRegistryRequest,
    PolicyRegistryRecord,
    RegisterPolicyRequest,
)
from document_core.schemas.taxonomy import normalize_categories
from document_core.services.document_tag_priors import assess_policy_tag_quality
from document_core.services.ingest import ingest_document
from document_core.services.registry import stable_policy_document_id
from document_core.services.registry_async import (
    delete_policy_async,
    get_policy_by_ref_async,
    list_policy_registry_async,
    register_policy_async,
)
from document_core.services.search import list_sections
from document_core.store.memory_store import get_store
from document_core.store.protocol import DocumentStore


def slug_policy_ref(prefix: str, title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (title or prefix).lower()).strip("-")
    return f"{prefix}-{slug}"[:96] or prefix


def sections_to_raw_text(sections: list[dict[str, Any]]) -> str:
    """Flatten structured sections into one document string (dev fixtures only)."""
    parts: list[str] = []
    for section in sections:
        title = str(section.get("title") or "").strip()
        text = str(section.get("text") or "").strip()
        if not text:
            continue
        if title:
            parts.append(f"{title}\n{text}")
        else:
            parts.append(text)
    return "\n\n".join(parts)


async def tombstone_tenant_policies(
    tenant_id: str,
    *,
    store: DocumentStore | None = None,
) -> list[str]:
    """Delete all indexed policies for a tenant."""
    _ = store
    refs: list[str] = []
    registry = await list_policy_registry_async(
        ListPolicyRegistryRequest(tenant_id=tenant_id, kind="policy"),
    )
    for entry in registry.policies:
        ref = entry.policy_ref
        if not ref:
            continue
        await delete_policy_async(DeletePolicyRequest(tenant_id=tenant_id, policy_ref=ref))
        refs.append(ref)
    return refs


def resolve_policy_identity(
    tenant_id: str,
    policy: PolicySyncInput,
    *,
    index: int,
) -> tuple[str, UUID]:
    """Internal policy_ref + document_id (Java sends document_id only)."""
    explicit_ref = (policy.policy_ref or "").strip()
    if explicit_ref:
        doc_id = policy.document_id or stable_policy_document_id(tenant_id, explicit_ref)
        if not isinstance(doc_id, UUID):
            doc_id = UUID(str(doc_id))
        return explicit_ref, doc_id
    if policy.document_id is not None:
        return str(policy.document_id), policy.document_id
    policy_ref = slug_policy_ref(f"playbook-{index}", policy.title or f"policy-{index}")
    return policy_ref, stable_policy_document_id(tenant_id, policy_ref)


async def _existing_registry_record(
    tenant_id: str,
    *,
    document_id: UUID,
    policy_ref: str,
    store: DocumentStore | None,
) -> PolicyRegistryRecord | None:
    doc_store = store or get_store()
    if hasattr(doc_store, "get_policy_registry_by_document_id_async"):
        existing = await doc_store.get_policy_registry_by_document_id_async(tenant_id, document_id)
    else:
        import asyncio

        existing = await asyncio.to_thread(
            doc_store.get_policy_registry_by_document_id,
            tenant_id,
            document_id,
        )
    if existing is not None:
        return existing
    return await get_policy_by_ref_async(tenant_id, policy_ref)


async def ingest_policy_structured(
    *,
    tenant_id: str,
    policy: PolicySyncInput,
    index: int = 1,
    source: str = "java-sync",
    store: DocumentStore | None = None,
) -> PolicySyncResult:
    """Register one policy, chunk/index it, return sync result."""
    policy_ref, document_id = resolve_policy_identity(tenant_id, policy, index=index)
    meta: dict[str, Any] = {"source": source, "policy_ref": policy_ref}

    existing = await _existing_registry_record(
        tenant_id,
        document_id=document_id,
        policy_ref=policy_ref,
        store=store,
    )
    if existing is not None:
        await delete_policy_async(
            DeletePolicyRequest(tenant_id=tenant_id, policy_ref=existing.policy_ref)
        )

    await register_policy_async(
        RegisterPolicyRequest(
            tenant_id=tenant_id,
            policy_ref=policy_ref,
            title=policy.title,
            document_id=document_id,
            source=source,
            metadata=meta,
        )
    )

    ingest = IngestRequest(
        tenant_id=tenant_id,
        document_id=document_id,
        title=policy.title,
        kind=DocumentKind.POLICY,
        text=policy.text,
        metadata=meta,
    )

    result = await ingest_document(ingest, store=store)
    record = await get_policy_by_ref_async(tenant_id, policy_ref)
    cats: list[str] = []
    tagger = "keyword"
    if record is not None:
        cats = normalize_categories((record.metadata or {}).get("categories"))
        tagger = str((record.metadata or {}).get("tagger") or tagger)

    indexed_sections = await list_sections(
        ListSectionsRequest(
            tenant_id=tenant_id,
            document_id=document_id,
            kind=DocumentKind.POLICY,
        ),
        store=store,
    )
    section_cats = [
        normalize_categories((section.metadata or {}).get("categories"))
        for section in indexed_sections
    ]
    tag_warnings = assess_policy_tag_quality(
        document_title=policy.title,
        section_categories=section_cats,
        tagger=tagger,
        document_union=cats,
    )
    merged_warnings = list(result.warnings or []) + tag_warnings

    return PolicySyncResult(
        policy_ref=policy_ref,
        document_id=str(document_id),
        title=policy.title,
        parent_count=result.parent_count,
        structure_confidence=str(result.structure_confidence.value),
        categories=cats,
        warnings=merged_warnings,
        auto_tagged=True,
        tagger=tagger,
    )


def policy_sync_input_from_dict(raw: dict[str, Any]) -> PolicySyncInput:
    """Build PolicySyncInput from loose dict (dev fixtures flatten sections → text)."""
    text = str(raw.get("text") or "").strip()
    if not text and raw.get("sections"):
        text = sections_to_raw_text(list(raw.get("sections") or []))
    doc_id_raw = raw.get("document_id")
    document_id = None
    if doc_id_raw:
        document_id = doc_id_raw if isinstance(doc_id_raw, UUID) else UUID(str(doc_id_raw).strip())
    policy_ref = str(raw.get("policy_ref") or "").strip() or None
    return PolicySyncInput(
        document_id=document_id,
        policy_ref=policy_ref,
        title=str(raw.get("title") or "Policy"),
        text=text,
    )


async def sync_policies(
    request: SyncPoliciesRequest,
    *,
    store: DocumentStore | None = None,
) -> SyncPoliciesResponse:
    """Index a batch of tenant playbooks (Java-facing sync path)."""
    tombstoned: list[str] = []
    if request.replace_policies:
        tombstoned = await tombstone_tenant_policies(request.tenant_id, store=store)

    results: list[PolicySyncResult] = []
    for index, policy in enumerate(request.policies, start=1):
        results.append(
            await ingest_policy_structured(
                tenant_id=request.tenant_id,
                policy=policy,
                index=index,
                source=request.source,
                store=store,
            )
        )

    primaries = [p.categories[0] for p in results if p.categories]
    dupes = sorted({c for c in primaries if primaries.count(c) > 1})
    weak_tag_policies = [
        p.title or p.policy_ref
        for p in results
        if any(
            "weak_tags" in w or "tagger=keyword" in w or w.startswith("unexpected_tags:")
            for w in (p.warnings or [])
        )
    ]

    return SyncPoliciesResponse(
        tenant_id=request.tenant_id,
        policies=results,
        tombstoned_policy_refs=tombstoned,
        preflight=SyncPoliciesPreflight(
            policies_synced=len(results),
            tombstoned_count=len(tombstoned),
            duplicate_primary_categories=dupes,
            weak_tag_count=len(weak_tag_policies),
            weak_tag_policies=weak_tag_policies,
        ),
    )
