"""Load tenant policy catalog entries from registry (Phase R2/R3)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from review_agent.clients.document_client import DocumentMCPClient


@dataclass(frozen=True)
class CatalogEntry:
    document_id: str
    policy_ref: str
    title: str
    aliases: list[str]
    topics: list[str]
    summary: str


def build_catalog_entry(record: Any) -> CatalogEntry:
    meta = dict(record.metadata or {})
    profile = meta.get("catalog_profile")
    if not isinstance(profile, dict):
        profile = {}
    title = str(record.title or "").strip()
    aliases = [str(a).strip() for a in (profile.get("aliases") or []) if str(a).strip()]
    if title and title not in aliases:
        aliases = [title, *aliases]
    topics = [str(t).strip().lower() for t in (profile.get("topics") or []) if str(t).strip()]
    summary = str(profile.get("summary") or title).strip()
    return CatalogEntry(
        document_id=str(record.document_id),
        policy_ref=str(record.policy_ref or "").strip(),
        title=title,
        aliases=aliases,
        topics=topics,
        summary=summary,
    )


async def load_catalog_entries(
    client: DocumentMCPClient,
    tenant_id: str,
    *,
    use_cache: bool = True,
) -> list[CatalogEntry]:
    if use_cache:
        from review_agent.config import get_settings
        from review_agent.services.routing_cache import get_catalog_snapshot

        settings = get_settings()
        if settings.routing_cache_enabled:
            snapshot = await get_catalog_snapshot(client, tenant_id, settings=settings)
            return list(snapshot.entries)

    response = await client.list_policy_registry(tenant_id, kind="policy", index_status="indexed")
    return [build_catalog_entry(record) for record in response.policies]


def indexed_doc_id_set(entries: list[CatalogEntry]) -> set[str]:
    return {entry.document_id for entry in entries}
