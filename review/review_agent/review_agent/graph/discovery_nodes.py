"""Graph nodes for contract routing and scoped policy discovery."""

from __future__ import annotations

import logging
from typing import Any

from review_agent.clients.document_client import DocumentMCPClient
from review_agent.config import get_settings
from review_agent.services.contract_routing import route_contract
from review_agent.services.policy_discovery import (
    discover_policies_from_topics,
    discovered_to_indexed_entries,
    parse_discovered_document_ids,
    seed_discovered_from_scope,
)
from review_agent.services.section_coverage import reviewable_sections
from review_agent.services.routing_tenant import obligation_routing_active
from review_agent.state.review_state import ReviewState

logger = logging.getLogger(__name__)


async def contract_routing_node(state: ReviewState, client: DocumentMCPClient) -> dict[str, Any]:
    settings = get_settings()
    sections = state.get("contract_sections") or []
    contract_text = " ".join(
        (section.text or "")[:500] for section in sections[:8]
    ).strip()
    if not contract_text:
        contract_text = str(state.get("contract_text") or "").strip()
    result, warnings = await route_contract(
        contract_text=contract_text,
        contract_sections=sections,
        contract_type_hint=state.get("contract_type"),
        settings=settings,
        client=client,
        tenant_id=state["tenant_id"],
    )

    updates: dict[str, Any] = {
        "contract_routing": result.model_dump(mode="json"),
        "warnings": warnings,
    }
    if result.contract_type and result.contract_type != "unknown":
        if not state.get("contract_type"):
            updates["contract_type"] = result.contract_type
    return updates


async def policy_discovery_node(state: ReviewState, client: DocumentMCPClient) -> dict[str, Any]:
    settings = get_settings()
    scope_ids = [
        str(doc_id).strip()
        for doc_id in (state.get("policy_document_ids") or [])
        if str(doc_id).strip()
    ]
    use_request_scope = settings.review_policy_scope == "request" or bool(scope_ids)

    warnings: list[str] = []
    discovery_meta: dict[str, Any] = {}

    if use_request_scope:
        if not scope_ids:
            raise ValueError("policy_document_ids is required when review_policy_scope=request")
        aggregated = await seed_discovered_from_scope(
            client,
            tenant_id=state["tenant_id"],
            scope_document_ids=scope_ids,
        )
        discovered = list(aggregated.values())
        doc_ids = parse_discovered_document_ids(discovered)
        if len(doc_ids) < len(scope_ids):
            warnings.append(
                f"scoped policy count {len(doc_ids)} < requested {len(scope_ids)}"
            )
            logger.warning(
                "policy scope mismatch tenant=%s requested=%d resolved=%d",
                state["tenant_id"],
                len(scope_ids),
                len(doc_ids),
            )
        discovery_mode = "request"
        discovery_meta = {"discovery_returned": len(doc_ids)}
    else:
        routing = state.get("contract_routing") or {}
        topics = list(routing.get("topics") or [])
        sections = state.get("contract_sections") or []
        reviewable = reviewable_sections(
            sections,
            min_chars=settings.review_min_section_chars,
        )
        discovered, discover_warnings, discovery_meta = await discover_policies_from_topics(
            client,
            tenant_id=state["tenant_id"],
            topics=topics,
            contract_type=state.get("contract_type"),
            policy_type=state.get("policy_type"),
            settings=settings,
            contract_sections=sections,
            reviewable_section_count=len(reviewable),
            scope_document_ids=None,
        )
        warnings.extend(discover_warnings)
        doc_ids = parse_discovered_document_ids(discovered)
        discovery_mode = "indexed"
        if not doc_ids:
            warnings.append(
                f"No tenant policies discovered for tenant '{state['tenant_id']}' — index playbooks first"
            )

    indexed_entries = discovered_to_indexed_entries(discovered)
    if obligation_routing_active(state["tenant_id"], settings):
        routing_candidates = [
            str(doc_id).strip()
            for doc_id in (state.get("obligation_routing_candidate_doc_ids") or [])
            if str(doc_id).strip()
        ]
        if routing_candidates:
            if scope_ids:
                scope_set = {str(doc_id).strip() for doc_id in scope_ids}
                routing_candidates = [doc_id for doc_id in routing_candidates if doc_id in scope_set]
            doc_ids = list(dict.fromkeys(list(doc_ids) + routing_candidates))
    return {
        "discovered_policies": [p.model_dump(mode="json") for p in discovered],
        "discovered_policy_document_ids": doc_ids,
        "policy_document_ids": doc_ids,
        "indexed_policies": indexed_entries,
        "discovery_warnings": warnings,
        "warnings": warnings,
        "compliance_stats": {
            **dict(state.get("compliance_stats") or {}),
            "discovery_returned": len(doc_ids),
            "discovery_scope_mode": discovery_mode,
            **discovery_meta,
        },
    }
