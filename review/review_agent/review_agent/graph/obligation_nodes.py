"""Obligation extraction graph node (Phase R1)."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from review_agent.clients.document_client import DocumentMCPClient
from review_agent.config import get_settings
from review_agent.schemas.obligation import ContractObligation
from review_agent.services.obligation_extract import extract_obligations_batch
from review_agent.services.routing_tenant import obligation_routing_active
from review_agent.services.section_filter import filter_review_sections
from review_agent.state.review_state import ReviewState


def _sort_pool_for_cap(pool: list[ContractObligation]) -> list[ContractObligation]:
    return sorted(
        pool,
        key=lambda ob: (
            ob.is_boilerplate,
            not bool(ob.explicit_policy_mentions),
            ob.obligation_id,
        ),
    )


def _cap_obligations_fair(
    obligations: list[ContractObligation],
    *,
    max_total: int,
    max_per_section: int,
    section_order: list[str],
) -> tuple[list[ContractObligation], int, list[str]]:
    if max_total <= 0 or len(obligations) <= max_total:
        return obligations, 0, []

    if max_per_section <= 0:
        trimmed = obligations[:max_total]
        dropped_ids = list(dict.fromkeys(ob.section_id for ob in obligations[max_total:]))
        return trimmed, len(obligations) - len(trimmed), dropped_ids

    by_section: dict[str, list[ContractObligation]] = defaultdict(list)
    for ob in obligations:
        by_section[ob.section_id].append(ob)

    order = section_order or list(dict.fromkeys(ob.section_id for ob in obligations))
    capped: list[ContractObligation] = []
    dropped_section_ids: list[str] = []

    for sid in order:
        pool = by_section.get(sid, [])
        if not pool:
            continue
        take = min(len(pool), max_per_section, max_total - len(capped))
        if take <= 0:
            if pool:
                dropped_section_ids.append(sid)
            continue
        capped.extend(pool[:take])
        if len(pool) > take:
            dropped_section_ids.append(sid)
        if len(capped) >= max_total:
            break

    represented = {ob.section_id for ob in capped}
    for sid in order:
        if sid in by_section and sid not in represented and sid not in dropped_section_ids:
            dropped_section_ids.append(sid)

    dropped_count = len(obligations) - len(capped)
    return capped, dropped_count, dropped_section_ids


def _cap_obligations_round_robin(
    obligations: list[ContractObligation],
    *,
    max_total: int,
    max_per_section: int,
    section_order: list[str],
) -> tuple[list[ContractObligation], int, list[str]]:
    if max_total <= 0 or len(obligations) <= max_total:
        return obligations, 0, []

    if max_per_section <= 0:
        trimmed = obligations[:max_total]
        dropped_ids = list(dict.fromkeys(ob.section_id for ob in obligations[max_total:]))
        return trimmed, len(obligations) - len(trimmed), dropped_ids

    by_section: dict[str, list[ContractObligation]] = defaultdict(list)
    for ob in obligations:
        by_section[ob.section_id].append(ob)

    order = section_order or list(dict.fromkeys(ob.section_id for ob in obligations))
    pools = {
        sid: _sort_pool_for_cap(by_section[sid])
        for sid in order
        if sid in by_section
    }
    indices = {sid: 0 for sid in pools}
    capped: list[ContractObligation] = []

    while len(capped) < max_total:
        progress = False
        for sid in order:
            if len(capped) >= max_total:
                break
            pool = pools.get(sid, [])
            idx = indices.get(sid, 0)
            if idx >= len(pool) or idx >= max_per_section:
                continue
            capped.append(pool[idx])
            indices[sid] = idx + 1
            progress = True
        if not progress:
            break

    dropped_section_ids: list[str] = []
    for sid in order:
        pool = pools.get(sid, [])
        if pool and indices.get(sid, 0) < len(pool):
            dropped_section_ids.append(sid)

    dropped_count = len(obligations) - len(capped)
    return capped, dropped_count, dropped_section_ids


async def obligation_extract_node(
    state: ReviewState,
    client: DocumentMCPClient,
) -> dict[str, Any]:
    _ = client
    settings = get_settings()
    if not settings.obligation_extract_enabled:
        return {}
    if not obligation_routing_active(state["tenant_id"], settings):
        stats = {
            "obligation_extract_skipped": True,
            "obligation_extract_skip_reason": "routing_off",
        }
        compliance_stats = dict(state.get("compliance_stats") or {})
        compliance_stats.update(stats)
        return {
            "obligations": [],
            "obligation_extract_stats": stats,
            "compliance_stats": compliance_stats,
        }

    sections = filter_review_sections(
        state.get("contract_sections") or [],
        min_chars=settings.review_min_section_chars,
    )
    if not sections:
        return {}

    result = await extract_obligations_batch(sections, settings=settings)
    obligations = list(result.obligations)
    warnings = list(result.warnings)
    cap_dropped_count = 0
    cap_dropped_section_ids: list[str] = []
    cap_mode = settings.obligation_cap_mode

    if (
        obligation_routing_active(state["tenant_id"], settings)
        and settings.max_obligations_per_review > 0
        and len(obligations) > settings.max_obligations_per_review
    ):
        cap_kwargs = dict(
            max_total=settings.max_obligations_per_review,
            max_per_section=settings.max_obligations_per_section,
            section_order=[section.section_id for section in sections],
        )
        if cap_mode == "round_robin":
            obligations, cap_dropped_count, cap_dropped_section_ids = _cap_obligations_round_robin(
                obligations,
                **cap_kwargs,
            )
        else:
            obligations, cap_dropped_count, cap_dropped_section_ids = _cap_obligations_fair(
                obligations,
                **cap_kwargs,
            )
        warnings.append(
            f"obligation list capped to {settings.max_obligations_per_review} "
            f"({cap_mode}, dropped={cap_dropped_count})"
        )

    obligation_payload = [item.model_dump(mode="json") for item in obligations]
    boilerplate_count = sum(1 for item in obligations if item.is_boilerplate)
    section_count = len({item.section_id for item in obligations}) or len(sections)
    stats = {
        "obligation_count": len(obligations),
        "boilerplate_obligation_count": boilerplate_count,
        "obligations_per_section_avg": round(len(obligations) / section_count, 2),
        "extract_batch_failures": result.extract_batch_failures,
        "extract_single_retries": result.extract_single_retries,
        "extract_single_recovered": result.extract_single_recovered,
        "extract_fallback_count": sum(1 for item in obligations if item.extract_source == "fallback"),
        "extract_llm_count": sum(1 for item in obligations if item.extract_source == "llm"),
        "obligation_cap_dropped_count": cap_dropped_count,
        "obligation_cap_dropped_section_ids": cap_dropped_section_ids,
        "obligation_cap_mode": cap_mode,
    }
    compliance_stats = dict(state.get("compliance_stats") or {})
    compliance_stats.update(stats)

    updates: dict[str, Any] = {
        "obligations": obligation_payload,
        "obligation_extract_stats": stats,
        "compliance_stats": compliance_stats,
    }
    if warnings:
        updates["warnings"] = warnings
    return updates
