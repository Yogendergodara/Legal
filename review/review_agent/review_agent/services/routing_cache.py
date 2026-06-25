"""Versioned in-process routing cache (Phase R9)."""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from dataclasses import dataclass

from review_agent.clients.document_client import DocumentMCPClient
from review_agent.config import ReviewSettings, get_settings
from review_agent.schemas.obligation import ContractObligation
from review_agent.schemas.routing_plan import ObligationRoutingPlan
from review_agent.services.catalog_registry import CatalogEntry, load_catalog_entries


@dataclass
class TenantCatalogSnapshot:
    tenant_id: str
    catalog_version: str
    entries: list[CatalogEntry]
    doc_id_set: set[str]
    loaded_at: float


_catalog_cache: dict[str, TenantCatalogSnapshot] = {}
_plan_cache: OrderedDict[str, ObligationRoutingPlan] = OrderedDict()


def _catalog_version(entries: list[CatalogEntry]) -> str:
    parts = sorted(f"{entry.document_id}:{entry.title}" for entry in entries)
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return digest


def clear_routing_cache() -> None:
    _catalog_cache.clear()
    _plan_cache.clear()


async def get_catalog_snapshot(
    client: DocumentMCPClient,
    tenant_id: str,
    *,
    settings: ReviewSettings | None = None,
) -> TenantCatalogSnapshot:
    cfg = settings or get_settings()
    now = time.monotonic()
    cached = _catalog_cache.get(tenant_id)
    if (
        cfg.routing_cache_enabled
        and cached is not None
        and (now - cached.loaded_at) < cfg.routing_cache_ttl_seconds
    ):
        return cached

    entries = await load_catalog_entries(client, tenant_id, use_cache=False)
    version = _catalog_version(entries)
    if cached is not None and cached.catalog_version == version and cfg.routing_cache_enabled:
        cached.loaded_at = now
        return cached

    snapshot = TenantCatalogSnapshot(
        tenant_id=tenant_id,
        catalog_version=version,
        entries=entries,
        doc_id_set={entry.document_id for entry in entries},
        loaded_at=now,
    )
    if cfg.routing_cache_enabled:
        _catalog_cache[tenant_id] = snapshot
    return snapshot


def plan_cache_key(
    *,
    tenant_id: str,
    catalog_version: str,
    obligation: ContractObligation,
) -> str:
    payload = f"{tenant_id}|{catalog_version}|{obligation.text}|{obligation.obligation_type}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def get_cached_plan(key: str, settings: ReviewSettings | None = None) -> ObligationRoutingPlan | None:
    cfg = settings or get_settings()
    if not cfg.routing_cache_enabled:
        return None
    return _plan_cache.get(key)


def set_cached_plan(
    key: str,
    plan: ObligationRoutingPlan,
    *,
    settings: ReviewSettings | None = None,
) -> None:
    cfg = settings or get_settings()
    if not cfg.routing_cache_enabled:
        return
    _plan_cache[key] = plan
    _plan_cache.move_to_end(key)
    while len(_plan_cache) > cfg.routing_plan_cache_max_entries:
        _plan_cache.popitem(last=False)
