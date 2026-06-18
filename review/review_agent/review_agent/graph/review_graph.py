"""Compiled LangGraph review pipeline."""

from __future__ import annotations

import uuid
from functools import partial

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from review_agent.clients.document_client import DocumentMCPClient
from review_agent.clients.memory_client import MemoryMCPClient
from review_agent.config import get_settings
from review_agent.graph.discovery_nodes import contract_routing_node, policy_discovery_node
from review_agent.graph.hybrid_nodes import (
    compliance_hybrid_merge_node,
    compliance_prescreen_node,
    compliance_review_pass1_node,
    compliance_review_pass2_node,
    policy_gap_retrieval_node,
)
from review_agent.graph.memory_nodes import load_memory_node, save_review_memory_node
from review_agent.graph.section_compare_nodes import (
    final_gap_verify_node,
    merge_section_findings_node,
    section_compare_llm_node,
)
from review_agent.graph.section_retrieval_nodes import section_policy_retrieval_node
from review_agent.graph.nodes import (
    clause_detection_node,
    compliance_review_node,
    contract_parser_node,
    grounding_node,
    index_policies_node,
    policy_plan_node,
    policy_retrieval_node,
    report_node,
)
from review_agent.state.review_state import ReviewState

_checkpointer = MemorySaver()


def build_review_graph(
    client: DocumentMCPClient,
    memory_client: MemoryMCPClient | None = None,
):
    """Build and compile the text-only compliance review graph."""
    graph = StateGraph(ReviewState)
    settings = get_settings()
    tenant_auto = settings.review_policy_source == "tenant_auto"
    section_first = settings.review_pipeline_mode == "section_first"

    graph.add_node("load_memory", partial(load_memory_node, memory_client=memory_client))
    graph.add_node("contract_parser", partial(contract_parser_node, client=client))
    graph.add_node("clause_detection", partial(clause_detection_node, client=client))
    graph.add_node("index_policies", partial(index_policies_node, client=client))
    graph.add_node("policy_plan", partial(policy_plan_node, client=client))
    graph.add_node("policy_retrieval", partial(policy_retrieval_node, client=client))

    if tenant_auto:
        graph.add_node("contract_routing", partial(contract_routing_node, client=client))
        graph.add_node("policy_discovery", partial(policy_discovery_node, client=client))

    if settings.compliance_mode == "hybrid":
        graph.add_node("compliance_prescreen", partial(compliance_prescreen_node, client=client))
        graph.add_node("compliance_review_pass1", partial(compliance_review_pass1_node, client=client))
        graph.add_node("policy_gap_retrieval", partial(policy_gap_retrieval_node, client=client))
        graph.add_node("compliance_review_pass2", partial(compliance_review_pass2_node, client=client))
        graph.add_node("compliance_hybrid_merge", partial(compliance_hybrid_merge_node, client=client))
    else:
        graph.add_node("compliance_review", partial(compliance_review_node, client=client))

    graph.add_node("grounding", partial(grounding_node, client=client))
    graph.add_node("report", partial(report_node, client=client))
    graph.add_node(
        "save_memory",
        partial(save_review_memory_node, memory_client=memory_client),
    )

    if section_first:
        graph.add_node(
            "section_policy_retrieval",
            partial(section_policy_retrieval_node, client=client),
        )
        graph.add_node("section_compare_llm", partial(section_compare_llm_node, client=client))
        graph.add_node(
            "merge_section_findings",
            partial(merge_section_findings_node, client=client),
        )
        graph.add_node("final_gap_verify", partial(final_gap_verify_node, client=client))

    graph.add_edge(START, "load_memory")
    graph.add_edge("load_memory", "contract_parser")
    graph.add_edge("contract_parser", "clause_detection")

    if section_first:
        if tenant_auto:
            graph.add_edge("clause_detection", "contract_routing")
            graph.add_edge("contract_routing", "policy_discovery")
            graph.add_edge("policy_discovery", "index_policies")
        else:
            graph.add_edge("clause_detection", "index_policies")
        graph.add_edge("index_policies", "section_policy_retrieval")
        graph.add_edge("section_policy_retrieval", "section_compare_llm")
        graph.add_edge("section_compare_llm", "merge_section_findings")
        graph.add_edge("merge_section_findings", "final_gap_verify")
        graph.add_edge("final_gap_verify", "grounding")
    elif tenant_auto:
        graph.add_edge("clause_detection", "contract_routing")
        graph.add_edge("contract_routing", "policy_discovery")
        graph.add_edge("policy_discovery", "index_policies")
        graph.add_edge("index_policies", "policy_plan")
        graph.add_edge("policy_plan", "policy_retrieval")
        if settings.compliance_mode == "hybrid":
            graph.add_edge("policy_retrieval", "compliance_prescreen")
            graph.add_edge("compliance_prescreen", "compliance_review_pass1")
            graph.add_edge("compliance_review_pass1", "policy_gap_retrieval")
            graph.add_edge("policy_gap_retrieval", "compliance_review_pass2")
            graph.add_edge("compliance_review_pass2", "compliance_hybrid_merge")
            graph.add_edge("compliance_hybrid_merge", "grounding")
        else:
            graph.add_edge("policy_retrieval", "compliance_review")
            graph.add_edge("compliance_review", "grounding")
    else:
        graph.add_edge("clause_detection", "index_policies")
        graph.add_edge("index_policies", "policy_plan")
        graph.add_edge("policy_plan", "policy_retrieval")
        if settings.compliance_mode == "hybrid":
            graph.add_edge("policy_retrieval", "compliance_prescreen")
            graph.add_edge("compliance_prescreen", "compliance_review_pass1")
            graph.add_edge("compliance_review_pass1", "policy_gap_retrieval")
            graph.add_edge("policy_gap_retrieval", "compliance_review_pass2")
            graph.add_edge("compliance_review_pass2", "compliance_hybrid_merge")
            graph.add_edge("compliance_hybrid_merge", "grounding")
        else:
            graph.add_edge("policy_retrieval", "compliance_review")
            graph.add_edge("compliance_review", "grounding")

    graph.add_edge("grounding", "report")
    graph.add_edge("report", "save_memory")
    graph.add_edge("save_memory", END)

    return graph.compile(checkpointer=_checkpointer)


async def run_review(
    *,
    client: DocumentMCPClient,
    tenant_id: str,
    contract_text: str,
    contract_title: str = "Contract",
    policy_texts: list[dict] | None = None,
    policy_document_ids: list[str] | None = None,
    policy_refs: list[str] | None = None,
    contract_type: str | None = None,
    policy_type: str | None = None,
    memory_client: MemoryMCPClient | None = None,
    memory_context: str = "",
    thread_id: str | None = None,
) -> ReviewState:
    """Run review graph with optional retrieval MCP memory + session checkpoint."""
    get_settings.cache_clear()
    session_id = thread_id or str(uuid.uuid4())
    graph = build_review_graph(client, memory_client=memory_client)
    initial: ReviewState = {
        "tenant_id": tenant_id,
        "contract_text": contract_text,
        "contract_title": contract_title,
        "policy_texts": policy_texts or [],
        "policy_document_ids": policy_document_ids or [],
        "policy_refs": policy_refs or [],
        "contract_type": contract_type,
        "policy_type": policy_type,
        "thread_id": session_id,
        "indexed_policies": [],
        "review_categories": [],
        "policy_hits_by_category": {},
        "contract_hits_by_category": {},
        "fetched_policy_refs": [],
        "policy_ref_by_document_id": {},
        "retrieval_meta_by_category": {},
        "alignment_by_category": {},
        "prescreen_findings": [],
        "deferred_category_ids": [],
        "pass1_findings": [],
        "pass2_findings": [],
        "gap_requests": [],
        "gap_hits_by_request": {},
        "compliance_stats": {},
        "contract_routing": {},
        "discovered_policies": [],
        "discovered_policy_document_ids": [],
        "discovery_warnings": [],
        "findings": [],
        "warnings": [],
        "memory_context": memory_context,
        "memory_hits": [],
        "section_retrieval_by_id": {},
        "section_review_sections": [],
        "section_compare_items": [],
    }
    config = {"configurable": {"thread_id": session_id}}
    return await graph.ainvoke(initial, config=config)
