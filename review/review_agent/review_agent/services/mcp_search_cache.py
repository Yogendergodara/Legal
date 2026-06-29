"""Per-review MCP policy search dedup cache (PF-1D)."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from document_core.schemas.chunk import RetrievalHit, SearchRequest

_cache: dict[str, list[RetrievalHit]] = {}
_hits = 0
_misses = 0


def clear_mcp_search_cache() -> None:
    global _hits, _misses
    _cache.clear()
    _hits = 0
    _misses = 0


def make_search_cache_key(
    endpoint: str,
    request: SearchRequest,
    *,
    categories: list[str] | None = None,
) -> str:
    doc_ids = sorted(str(doc_id) for doc_id in (request.document_ids or []))
    payload: dict[str, Any] = {
        "endpoint": endpoint,
        "tenant_id": request.tenant_id,
        "query": request.query,
        "document_ids": doc_ids,
        "top_k": request.top_k,
        "contract_type": request.contract_type,
        "policy_type": request.policy_type,
        "categories": sorted(categories or []),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_cached_hits(key: str) -> list[RetrievalHit] | None:
    hits = _cache.get(key)
    if hits is None:
        return None
    global _hits
    _hits += 1
    return [hit.model_copy(deep=True) for hit in hits]


def set_cached_hits(key: str, hits: list[RetrievalHit]) -> None:
    global _misses
    _misses += 1
    _cache[key] = [hit.model_copy(deep=True) for hit in hits]


def cache_stats() -> dict[str, int | float]:
    total = _hits + _misses
    rate = round(_hits / total, 3) if total else 0.0
    return {
        "mcp_cache_hits": _hits,
        "mcp_cache_misses": _misses,
        "mcp_cache_hit_rate": rate,
    }
