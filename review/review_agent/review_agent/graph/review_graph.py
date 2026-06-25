"""Compiled LangGraph review pipeline — section-first only."""

from __future__ import annotations

import logging
import os
import time
import uuid
from functools import partial

from langgraph.graph import END, START, StateGraph

from review_agent.clients.document_client import DocumentMCPClient
from review_agent.clients.memory_client import MemoryMCPClient
from review_agent.config import build_runtime_settings_snapshot, get_settings
from review_agent.graph.obligation_nodes import obligation_extract_node
from review_agent.graph.obligation_compare_nodes import obligation_compare_node
from review_agent.graph.obligation_retrieval_nodes import (
    evidence_sufficiency_node,
    obligation_retrieval_node,
)
from review_agent.graph.routing_nodes import catalog_match_node, semantic_route_node
from review_agent.graph.discovery_nodes import contract_routing_node, policy_discovery_node
from review_agent.graph.memory_nodes import load_memory_node, save_review_memory_node
from review_agent.graph.section_compare_nodes import (
    final_gap_verify_node,
    merge_section_findings_node,
    section_compare_llm_node,
)
from review_agent.graph.section_retrieval_nodes import section_policy_retrieval_node
from review_agent.graph.nodes import (
    clause_detection_node,
    contract_parser_node,
    grounding_node,
    index_policies_node,
    report_node,
)
from review_agent.graph.review_inputs import validate_review_inputs
from review_agent.observability.context import bind_review_context, clear_review_context
from review_agent.observability.logging import configure_review_logging
from review_agent.observability.metrics import configure_metrics, record_review_duration
from review_agent.observability.timing import wrap_node
from review_agent.services.review_preflight import run_review_preflight
from review_agent.state.review_state import ReviewState

logger = logging.getLogger(__name__)


def _add_timed_node(graph: StateGraph, name: str, fn, **kwargs) -> None:
    graph.add_node(name, wrap_node(name, partial(fn, **kwargs)))


def build_review_graph(
    client: DocumentMCPClient,
    memory_client: MemoryMCPClient | None = None,
):
    """Build section-first compliance review graph."""
    graph = StateGraph(ReviewState)

    _add_timed_node(graph, "load_memory", load_memory_node, memory_client=memory_client)
    _add_timed_node(graph, "contract_parser", contract_parser_node, client=client)
    _add_timed_node(graph, "clause_detection", clause_detection_node, client=client)
    _add_timed_node(graph, "obligation_extract", obligation_extract_node, client=client)
    _add_timed_node(graph, "semantic_route", semantic_route_node, client=client)
    _add_timed_node(graph, "catalog_match", catalog_match_node, client=client)
    _add_timed_node(graph, "contract_routing", contract_routing_node, client=client)
    _add_timed_node(graph, "policy_discovery", policy_discovery_node, client=client)
    _add_timed_node(graph, "index_policies", index_policies_node, client=client)
    _add_timed_node(graph, "obligation_retrieval", obligation_retrieval_node, client=client)
    _add_timed_node(graph, "evidence_sufficiency", evidence_sufficiency_node, client=client)
    _add_timed_node(graph, "obligation_compare", obligation_compare_node, client=client)
    _add_timed_node(
        graph,
        "section_policy_retrieval",
        section_policy_retrieval_node,
        client=client,
    )
    _add_timed_node(graph, "section_compare_llm", section_compare_llm_node, client=client)
    _add_timed_node(
        graph,
        "merge_section_findings",
        merge_section_findings_node,
        client=client,
    )
    _add_timed_node(graph, "final_gap_verify", final_gap_verify_node, client=client)
    _add_timed_node(graph, "grounding", grounding_node, client=client)
    _add_timed_node(graph, "report", report_node, client=client)
    _add_timed_node(graph, "save_memory", save_review_memory_node, memory_client=memory_client)

    graph.add_edge(START, "load_memory")
    graph.add_edge("load_memory", "contract_parser")
    graph.add_edge("contract_parser", "clause_detection")
    graph.add_edge("clause_detection", "obligation_extract")
    graph.add_edge("obligation_extract", "semantic_route")
    graph.add_edge("semantic_route", "catalog_match")
    graph.add_edge("catalog_match", "contract_routing")
    graph.add_edge("contract_routing", "policy_discovery")
    graph.add_edge("policy_discovery", "index_policies")
    graph.add_edge("index_policies", "obligation_retrieval")
    graph.add_edge("obligation_retrieval", "evidence_sufficiency")
    graph.add_edge("evidence_sufficiency", "obligation_compare")
    graph.add_edge("obligation_compare", "section_policy_retrieval")
    graph.add_edge("section_policy_retrieval", "section_compare_llm")
    graph.add_edge("section_compare_llm", "merge_section_findings")
    graph.add_edge("merge_section_findings", "final_gap_verify")
    graph.add_edge("final_gap_verify", "grounding")
    graph.add_edge("grounding", "report")
    graph.add_edge("report", "save_memory")
    graph.add_edge("save_memory", END)

    return graph.compile()


async def run_review(
    *,
    client: DocumentMCPClient,
    tenant_id: str,
    contract_document_id: str | None = None,
    contract_text: str | None = None,
    contract_title: str = "Contract",
    policy_document_ids: list[str] | None = None,
    policy_scope: str | None = None,
    contract_type: str | None = None,
    policy_type: str | None = None,
    memory_client: MemoryMCPClient | None = None,
    memory_context: str = "",
    thread_id: str | None = None,
) -> ReviewState:
    """Run section-first review graph."""
    get_settings.cache_clear()
    settings = get_settings()
    scope = (policy_scope or settings.review_policy_scope or "indexed").strip().lower()
    if scope in ("indexed", "session"):
        os.environ["REVIEW_POLICY_SCOPE"] = "indexed" if scope == "indexed" else "request"
        get_settings.cache_clear()
        settings = get_settings()

    configure_review_logging(json_logs=settings.review_log_json)
    configure_metrics(settings.review_metrics_enabled)

    parsed_doc_id, normalized_policy_ids, input_warnings = validate_review_inputs(
        contract_document_id=contract_document_id,
        contract_text=contract_text,
        policy_document_ids=policy_document_ids,
        policy_scope=settings.review_policy_scope,
    )
    preflight_warnings = await run_review_preflight(
        client,
        preflight_enabled=settings.review_preflight_enabled,
        tenant_id=tenant_id,
        policy_document_ids=normalized_policy_ids or None,
        contract_document_id=parsed_doc_id,
    )
    session_id = thread_id or str(uuid.uuid4())
    bind_review_context(tenant_id=tenant_id, thread_id=session_id)
    logger.info("review_started tenant_id=%s thread_id=%s", tenant_id, session_id)

    wall_start = time.perf_counter()
    try:
        graph = build_review_graph(client, memory_client=memory_client)
        initial: ReviewState = {
            "tenant_id": tenant_id,
            "contract_document_id": parsed_doc_id,
            "contract_text": (contract_text or "").strip() or None,
            "contract_title": contract_title,
            "policy_document_ids": list(normalized_policy_ids),
            "contract_type": contract_type,
            "policy_type": policy_type,
            "thread_id": session_id,
            "indexed_policies": [],
            "compliance_stats": {
                "runtime_settings": build_runtime_settings_snapshot(settings),
            },
            "contract_routing": {},
            "discovered_policies": [],
            "discovered_policy_document_ids": [],
            "discovery_warnings": [],
            "findings": [],
            "warnings": input_warnings + preflight_warnings,
            "memory_context": memory_context,
            "memory_hits": [],
            "section_retrieval_by_id": {},
            "section_review_sections": [],
            "section_compare_items": [],
            "gap_section_ids": [],
            "unclear_finding_ids": [],
            "unclear_recompare_finding_ids": [],
            "conflict_pairs": [],
            "section_coverage": {},
            "obligations": [],
            "obligation_extract_stats": {},
            "obligation_routing_by_id": {},
            "obligation_catalog_match_by_id": {},
            "obligation_retrieval_by_id": {},
            "obligation_evidence_by_id": {},
            "obligation_compare_items": [],
            "obligation_findings": [],
        }
        config = {"configurable": {"thread_id": session_id}}
        result = await graph.ainvoke(initial, config=config)
        stats = dict(result.get("compliance_stats") or {})
        wall_ms = round((time.perf_counter() - wall_start) * 1000, 2)
        stats["review_wall_ms"] = wall_ms
        result["compliance_stats"] = stats
        record_review_duration(wall_ms / 1000.0)
        logger.info("review_completed wall_ms=%s", wall_ms)
        return result
    finally:
        clear_review_context()
