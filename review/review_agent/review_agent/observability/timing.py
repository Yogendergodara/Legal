"""Graph node timing (Phase 31) and parallel compliance_stats reducers (PF-1C)."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from review_agent.observability.context import set_current_node
from review_agent.observability.metrics import record_node_duration

# Nested dict keys merged shallowly (union) when parallel branches both patch stats.
_NESTED_DICT_KEYS = frozenset(
    {
        "node_timings_ms",
        "retrieval_paths_used",
        "retrieval_path_hits",
        "obligation_evidence_skip_by_reason",
        "obligation_pipeline_funnel",
        "routing_summary",
        "runtime_settings",
        "compare_hit_selection",
        "mcp_cache",
    }
)


def _merge_nested_dict(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    merged = dict(left)
    for key, rval in right.items():
        lval = merged.get(key)
        if isinstance(lval, dict) and isinstance(rval, dict):
            merged[key] = {**lval, **rval}
        else:
            merged[key] = rval
    return merged


def merge_compliance_stats(
    left: dict[str, Any] | None,
    right: dict[str, Any] | None,
) -> dict[str, Any]:
    """LangGraph reducer for parallel hybrid branches (PF-1C §8.2)."""
    if not left:
        return dict(right or {})
    if not right:
        return dict(left)

    merged = dict(left)
    for key, rval in right.items():
        lval = merged.get(key)
        if key == "compliance_mode" and lval and rval and lval != rval:
            merged[key] = "hybrid"
            continue
        if key in _NESTED_DICT_KEYS and isinstance(lval, dict) and isinstance(rval, dict):
            merged[key] = _merge_nested_dict(lval, rval)
            continue
        if isinstance(lval, dict) and isinstance(rval, dict):
            merged[key] = _merge_nested_dict(lval, rval)
            continue
        merged[key] = rval
    return merged


def _finding_id(item: Any) -> str | None:
    fid = getattr(item, "finding_id", None)
    if fid:
        return str(fid)
    if isinstance(item, dict) and item.get("finding_id"):
        return str(item["finding_id"])
    return None


def _finding_meta(item: Any) -> dict[str, Any]:
    meta = getattr(item, "metadata", None)
    if isinstance(meta, dict):
        return meta
    if isinstance(item, dict):
        raw = item.get("metadata")
        return raw if isinstance(raw, dict) else {}
    return {}


def _finding_section_id(item: Any) -> str | None:
    sid = getattr(item, "contract_section_id", None)
    if sid:
        return str(sid)
    if isinstance(item, dict) and item.get("contract_section_id"):
        return str(item["contract_section_id"])
    return None


_GAP_SUPERSEDE_TYPES = frozenset({"no_policy", "compare_omitted", "coverage_backfill"})


def merge_id_lists(
    left: list[str] | None,
    right: list[str] | None,
) -> list[str]:
    """LangGraph reducer: ordered union of string ids."""
    if not left:
        return list(right or [])
    if not right:
        return list(left)
    seen = set(left)
    merged = list(left)
    for item in right:
        if item not in seen:
            seen.add(item)
            merged.append(item)
    return merged


def merge_conflict_pairs(
    left: list[list[str]] | None,
    right: list[list[str]] | None,
) -> list[list[str]]:
    """LangGraph reducer: union conflict pairs (order-stable, deduped)."""
    if not left:
        return [list(pair) for pair in (right or [])]
    if not right:
        return [list(pair) for pair in left]
    seen: set[tuple[str, str]] = set()
    merged: list[list[str]] = []
    for pair in left + right:
        if len(pair) != 2:
            continue
        a, b = str(pair[0]), str(pair[1])
        key = (a, b) if a <= b else (b, a)
        if key in seen:
            continue
        seen.add(key)
        merged.append([a, b])
    return merged


def merge_dict_shallow(
    left: dict[str, Any] | None,
    right: dict[str, Any] | None,
) -> dict[str, Any]:
    """LangGraph reducer: shallow dict merge; right wins per key."""
    if not left:
        return dict(right or {})
    if not right:
        return dict(left)
    return {**left, **right}


def _finding_status(item: Any) -> str:
    status = getattr(item, "status", None)
    if status is not None:
        return str(getattr(status, "value", status))
    if isinstance(item, dict) and item.get("status") is not None:
        raw = item["status"]
        return str(getattr(raw, "value", raw))
    return ""


def merge_findings(
    left: list[Any] | None,
    right: list[Any] | None,
) -> list[Any]:
    """LangGraph reducer: dedupe by finding_id; right wins; gap-verify aware."""
    if not left:
        return list(right or [])
    if not right:
        return list(left)

    final_verify_sections = {
        sid
        for item in right
        if (sid := _finding_section_id(item)) and _finding_meta(item).get("final_verify")
    }
    right_ids = {fid for item in right if (fid := _finding_id(item))}

    by_id: dict[str, Any] = {}
    anon = 0
    for item in left:
        fid = _finding_id(item)
        if fid and fid in right_ids:
            continue
        if fid and final_verify_sections:
            meta = _finding_meta(item)
            sid = _finding_section_id(item)
            if sid in final_verify_sections:
                if meta.get("gap_type") in _GAP_SUPERSEDE_TYPES:
                    continue
                if _finding_status(item) == "INCONCLUSIVE":
                    continue
        if fid:
            by_id[fid] = item
        else:
            by_id[f"__anon_{anon}"] = item
            anon += 1
    for item in right:
        fid = _finding_id(item)
        if fid:
            by_id[fid] = item
        else:
            by_id[f"__anon_{anon}"] = item
            anon += 1
    return list(by_id.values())


def merge_node_timing(
    state: dict[str, Any],
    out: dict[str, Any],
    node_name: str,
    elapsed_ms: float,
) -> dict[str, Any]:
    from review_agent.resilience.failure_policy import enrich_compliance_stats_with_posture

    stats = merge_compliance_stats(
        state.get("compliance_stats"),
        out.get("compliance_stats"),
    )
    stats = enrich_compliance_stats_with_posture(stats)
    timings = dict(stats.get("node_timings_ms") or {})
    timings[node_name] = elapsed_ms
    stats["node_timings_ms"] = timings
    merged = dict(out)
    merged["compliance_stats"] = stats
    return merged


def wrap_node(node_name: str, fn: Callable[..., Awaitable[dict[str, Any] | None]]):
    async def wrapped(state: dict[str, Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
        set_current_node(node_name)
        start = time.perf_counter()
        try:
            out = await fn(state, *args, **kwargs)
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            record_node_duration(node_name, elapsed_ms / 1000.0)
            return merge_node_timing(state, out or {}, node_name, elapsed_ms)
        except Exception:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            record_node_duration(node_name, elapsed_ms / 1000.0)
            raise

    return wrapped
