"""Graph nodes for contract-first policy discovery (Phase 6)."""

from __future__ import annotations

from typing import Any

from review_agent.clients.document_client import DocumentMCPClient
from review_agent.config import get_settings
from review_agent.services.contract_routing import route_contract
from review_agent.services.policy_discovery import (
    discover_policies_from_topics,
    discovered_to_indexed_entries,
    parse_discovered_document_ids,
)
from review_agent.state.review_state import ReviewState


def _explicit_policies_in_request(state: ReviewState) -> bool:
    """User supplied policies — skip auto-discovery."""
    if state.get("policy_texts"):
        return True
    if state.get("policy_refs"):
        return True
    if state.get("policy_document_ids"):
        return True
    return False


async def contract_routing_node(state: ReviewState, client: DocumentMCPClient) -> dict[str, Any]:
    settings = get_settings()
    if settings.review_policy_source != "tenant_auto":
        return {}

    result, warnings = await route_contract(
        contract_text=state.get("contract_text") or "",
        contract_sections=state.get("contract_sections") or [],
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
    if settings.review_policy_source != "tenant_auto":
        return {}

    if _explicit_policies_in_request(state):
        return {
            "warnings": [
                "tenant_auto discovery skipped: explicit policies/refs/document_ids in request."
            ],
        }

    routing = state.get("contract_routing") or {}
    topics = routing.get("topics") or []
    contract_type = state.get("contract_type") or routing.get("contract_type")

    discovered, warnings = await discover_policies_from_topics(
        client,
        tenant_id=state["tenant_id"],
        topics=topics,
        contract_type=contract_type,
        policy_type=state.get("policy_type"),
        settings=settings,
    )

    doc_ids = parse_discovered_document_ids(discovered)
    indexed_entries = discovered_to_indexed_entries(discovered)

    return {
        "discovered_policies": [p.model_dump(mode="json") for p in discovered],
        "discovered_policy_document_ids": doc_ids,
        "policy_document_ids": doc_ids,
        "indexed_policies": indexed_entries,
        "discovery_warnings": warnings,
        "warnings": warnings,
    }
