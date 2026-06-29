"""Match obligation routing plans to tenant policy catalog (Phase R3)."""

from __future__ import annotations

import re

from document_core.schemas.policy_catalog import CatalogSearchRequest
from review_agent.clients.document_client import DocumentMCPClient
from review_agent.config import ReviewSettings, get_settings
from review_agent.schemas.routing_plan import CatalogMatchResult, ObligationRoutingPlan
from review_agent.services.catalog_registry import CatalogEntry
from review_agent.services.routing_limits import catalog_search_calls, increment_catalog_search_calls

_TOKEN_RE = re.compile(r"[a-z0-9]{3,}")


def _title_tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def _title_token_overlap(text_a: str, text_b: str) -> float:
    a = _title_tokens(text_a)
    b = _title_tokens(text_b)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _unique_queries(*groups: str | list[str] | None, cap: int) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for group in groups:
        items = [group] if isinstance(group, str) else list(group or [])
        for item in items:
            query = str(item).strip()
            if not query or query in seen:
                continue
            seen.add(query)
            ordered.append(query)
            if len(ordered) >= cap:
                return ordered
    return ordered


def _catalog_queries(
    plan: ObligationRoutingPlan,
    *,
    obligation_text: str,
    section_title: str,
    fallback_enabled: bool,
    cap: int,
) -> list[str]:
    base = [q.strip() for q in (plan.search_queries or [plan.intent]) if str(q).strip()]
    if not fallback_enabled:
        return base[:3]
    extras: list[str] = []
    intent = (plan.intent or "").strip()
    if intent:
        extras.append(intent)
    if section_title.strip():
        extras.append(f"{section_title.strip()} {intent}".strip()[:200])
    if obligation_text.strip():
        extras.append(obligation_text.strip()[:200])
    return _unique_queries(base, extras, cap=cap)


def _title_overlap_candidates(
    *,
    obligation_text: str,
    catalog_entries: list[CatalogEntry],
    allowed: set[str],
    min_score: float,
) -> dict[str, float]:
    if not obligation_text.strip() or not catalog_entries:
        return {}
    scored: dict[str, float] = {}
    for entry in catalog_entries:
        doc_id = entry.document_id
        if allowed and doc_id not in allowed:
            continue
        labels = [entry.title, *(entry.aliases or []), entry.summary]
        overlap = max(
            (_title_token_overlap(obligation_text, label) for label in labels if label),
            default=0.0,
        )
        if overlap >= min_score:
            scored[doc_id] = max(scored.get(doc_id, 0.0), overlap)
    return scored


async def match_obligation_to_catalog(
    plan: ObligationRoutingPlan,
    *,
    client: DocumentMCPClient,
    tenant_id: str,
    catalog_entries: list[CatalogEntry],
    allowed_doc_ids: set[str] | None,
    settings: ReviewSettings | None = None,
    obligation_text: str = "",
    section_title: str = "",
) -> CatalogMatchResult:
    cfg = settings or get_settings()
    allowed = allowed_doc_ids or set()
    fallback_enabled = cfg.catalog_match_obligation_fallback_enabled

    if plan.routing_source == "skipped_boilerplate":
        return CatalogMatchResult(
            obligation_id=plan.obligation_id,
            routing_source="ipc",
            confidence=plan.confidence,
            route_decision="ipc",
        )

    # PR-01 / PR-06 — named policy mentions still run catalog search despite low planner confidence
    if (
        plan.confidence < cfg.routing_ipc_max_confidence
        and not plan.explicit_policy_mentions
    ):
        return CatalogMatchResult(
            obligation_id=plan.obligation_id,
            routing_source="ipc",
            confidence=plan.confidence,
            route_decision="ipc",
        )

    if plan.routing_source == "registry_alias":
        doc_ids = [doc_id for doc_id in plan.resolved_document_ids if doc_id in allowed]
        rejected = [
            {"document_id": doc_id, "reason": "not_in_tenant_registry"}
            for doc_id in plan.resolved_document_ids
            if doc_id not in allowed
        ]
        return CatalogMatchResult(
            obligation_id=plan.obligation_id,
            candidate_doc_ids=doc_ids,
            candidate_scores={doc_id: 1.0 for doc_id in doc_ids},
            routing_source="registry_alias",
            confidence=1.0,
            rejected=rejected,
            route_decision="compare" if doc_ids else "ipc",
        )

    queries = _catalog_queries(
        plan,
        obligation_text=obligation_text,
        section_title=section_title,
        fallback_enabled=fallback_enabled,
        cap=cfg.catalog_match_max_queries,
    )
    scores: dict[str, float] = {}
    for query in queries:
        if (
            cfg.max_catalog_search_calls_per_review > 0
            and catalog_search_calls() >= cfg.max_catalog_search_calls_per_review
        ):
            break
        increment_catalog_search_calls()
        hits = await client.search_policy_catalog(
            CatalogSearchRequest(
                tenant_id=tenant_id,
                query=query,
                top_k=cfg.catalog_match_top_k,
            )
        )
        for hit in hits:
            doc_id = str(hit.document_id)
            scores[doc_id] = max(scores.get(doc_id, 0.0), float(hit.score))

    rejected: list[dict[str, str]] = []
    fenced: dict[str, float] = {}
    for doc_id, score in scores.items():
        if allowed and doc_id not in allowed:
            rejected.append({"document_id": doc_id, "reason": "not_in_tenant_registry"})
        else:
            fenced[doc_id] = score

    if not fenced and fallback_enabled and catalog_entries and obligation_text.strip():
        for doc_id, score in _title_overlap_candidates(
            obligation_text=obligation_text,
            catalog_entries=catalog_entries,
            allowed=allowed,
            min_score=cfg.catalog_match_title_min_score,
        ).items():
            fenced[doc_id] = score

    sorted_ids = sorted(fenced.keys(), key=lambda doc_id: fenced[doc_id], reverse=True)
    capped = sorted_ids[: cfg.catalog_match_max_candidates]
    candidate_scores = {doc_id: fenced[doc_id] for doc_id in capped}
    top_score = max(candidate_scores.values()) if candidate_scores else 0.0
    min_score = cfg.catalog_match_min_score
    marginal_floor = min_score * 0.85

    route_decision = "ipc"
    if not capped:
        route_decision = "ipc"
    elif top_score >= min_score:
        route_decision = "compare"
    elif top_score >= marginal_floor:
        # PR-05 — marginal fenced hit: retrieve + compare without extra expand round
        route_decision = "compare"
    elif cfg.evidence_compare_on_catalog_candidates:
        route_decision = "expand"
    else:
        route_decision = "ipc"

    if (
        route_decision == "compare"
        and 0.60 <= plan.confidence < cfg.routing_compare_min_confidence
    ):
        route_decision = "expand"

    return CatalogMatchResult(
        obligation_id=plan.obligation_id,
        candidate_doc_ids=capped,
        candidate_scores=candidate_scores,
        routing_source="catalog_search",
        confidence=plan.confidence,
        queries_used=queries,
        rejected=rejected,
        route_decision=route_decision,
    )
