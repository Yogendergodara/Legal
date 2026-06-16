
"""Full Multi-Agent Research System

This module integrates all components of the research system:
- User clarification and scoping
- Research brief generation  
- Multi-agent research coordination
- Final report generation

The system orchestrates the complete research workflow from initial user
input through final report delivery.
"""

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

# ===== Config =====
from deep_research_from_scratch.config import config as app_config
from deep_research_from_scratch.model_config import (
    ainvoke_with_retry,
    fit_writer_prompt,
    get_chat_model,
    is_rate_limit_error,
)
from deep_research_from_scratch.multi_agent_supervisor import supervisor_agent
from deep_research_from_scratch.prompts import final_report_generation_prompt
from deep_research_from_scratch.report_verification import (
    finalize_report,
    route_after_verify,
    verify_report,
)
from deep_research_from_scratch.research_agent_scope import (
    clarify_with_user,
    compact_conversation,
    load_memory,
    write_research_brief,
)
from deep_research_from_scratch.research_bootstrap import bootstrap_legal_research
from deep_research_from_scratch.report_sources import build_case_digest
from deep_research_from_scratch.source_registry import (
    RetrievedSource,
    build_verification_corpus,
    count_fetches,
    filter_citable_sources,
    format_writer_source_registry,
)
from deep_research_from_scratch.state_scope import AgentInputState, AgentState
from deep_research_from_scratch.utils import get_today_str
from typing_extensions import Literal

import asyncio

writer_model = get_chat_model("writer", max_tokens=32000)

# ===== FINAL REPORT GENERATION =====


def _collect_sources(state: AgentState) -> list[RetrievedSource]:
    sources: list[RetrievedSource] = []
    for item in state.get("retrieved_sources") or []:
        sources.append(item if isinstance(item, RetrievedSource) else RetrievedSource(**item))
    return sources


def _trim_findings(findings: str, char_budget: int) -> str:
    if len(findings) <= char_budget:
        return findings
    return (
        findings[:char_budget]
        + "\n\n[Findings truncated — cite ONLY from the Permitted Source Registry below.]"
    )

# ===== BOOTSTRAP RESEARCH (deterministic, no LLM) =====


async def bootstrap_research(state: AgentState, config: RunnableConfig):
    """Pre-fetch primary sources via retrieval MCP before the LLM supervisor runs."""
    user_query = ""
    for message in reversed(state.get("messages", []) or []):
        if getattr(message, "type", None) == "human":
            user_query = str(getattr(message, "content", "") or "")
            break

    brief = state.get("research_brief") or ""
    note, raw, sources = bootstrap_legal_research(brief, user_query)
    if not note:
        return {}

    return {
        "notes": [note],
        "raw_notes": [raw],
        "retrieved_sources": sources,
    }


def route_after_bootstrap(
    state: AgentState,
) -> Literal["supervisor_subgraph", "final_report_generation"]:
    """Skip the LLM supervisor when fast mode has enough fetched primary sources."""
    if not app_config.FAST_RESEARCH_MODE:
        return "supervisor_subgraph"
    sources = _collect_sources(state)
    _, primary_fetches = count_fetches(sources)
    if primary_fetches >= app_config.FAST_MODE_MIN_FETCHES:
        return "final_report_generation"
    return "supervisor_subgraph"


# ===== FINAL REPORT GENERATION =====


async def _invoke_writer(
    prompt: str,
    *,
    findings: str,
    safe_max_tokens: int | None,
) -> str:
    """Call the writer LLM with 429 backoff and a lighter fallback pass."""
    # (findings_char_budget, max_tokens, cooldown_seconds, max_retries)
    attempts: list[tuple[int, int | None, float, int]] = [
        (app_config.LLM_FINDINGS_CHAR_BUDGET, safe_max_tokens, 0.0, 8),
        (12_000, 2048, 20.0, 5),
        (8_000, 1536, 35.0, 4),
    ]
    last_exc: Exception | None = None

    for idx, (char_budget, max_tokens, cooldown, retries) in enumerate(attempts):
        if cooldown > 0:
            await asyncio.sleep(cooldown)

        candidate_prompt = prompt
        if idx > 0:
            trimmed = _trim_findings(findings, char_budget)
            candidate_prompt = prompt.replace(findings, trimmed, 1)

        bound_model = (
            writer_model.bind(max_tokens=max_tokens)
            if max_tokens is not None
            else writer_model
        )
        try:
            result = await ainvoke_with_retry(
                bound_model,
                [HumanMessage(content=candidate_prompt)],
                max_retries=retries,
            )
            content = str(getattr(result, "content", "") or "").strip()
            if content:
                return content
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if not is_rate_limit_error(exc) or idx >= len(attempts) - 1:
                raise

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Writer returned empty content after all attempts.")


async def final_report_generation(state: AgentState, config: RunnableConfig):
    """Final report generation node.

    Drafts the memorandum from the research findings. On a revision pass it
    incorporates the verification reviewer's feedback. Delivery + persistence
    happen in ``finalize_report`` (after verification), so this node only
    produces the draft.
    """
    notes = state.get("notes", [])
    sources = filter_citable_sources(_collect_sources(state))
    findings_text = "\n".join(notes)
    if not findings_text.strip() and sources:
        findings_text = build_verification_corpus(
            [], state.get("raw_notes", []), sources
        )
    findings = _trim_findings(findings_text, app_config.LLM_FINDINGS_CHAR_BUDGET)
    source_registry = format_writer_source_registry(sources)
    case_digest = build_case_digest(sources)

    # On a revise pass, feed the reviewer's required fixes back to the writer.
    verification = state.get("verification")
    if verification is not None and verification.required_fixes:
        verification_feedback = verification.required_fixes
    else:
        verification_feedback = "This is the first draft - no reviewer feedback yet."

    final_report_prompt = final_report_generation_prompt.format(
        research_brief=state.get("research_brief", ""),
        findings=findings,
        source_registry=source_registry,
        case_digest=case_digest,
        date=get_today_str(),
        verification_feedback=verification_feedback,
    )

    final_report_prompt, safe_max_tokens = fit_writer_prompt(
        final_report_prompt,
        findings=findings,
        trim_findings=_trim_findings,
        requested_max_tokens=32000,
    )

    try:
        content = await _invoke_writer(
            final_report_prompt,
            findings=findings,
            safe_max_tokens=safe_max_tokens,
        )
    except Exception as e:  # noqa: BLE001 - degrade gracefully; verification will flag/caveat it
        if is_rate_limit_error(e):
            content = (
                "# Legal Research Memorandum\n\n"
                "**Mistral API rate limit reached.** The research sources were "
                "retrieved successfully, but the memorandum could not be written "
                "after several retries.\n\n"
                "Please wait 1–2 minutes and submit the same query again — your "
                "session may continue from cached research. "
                "No legal conclusions should be drawn from this message."
            )
        else:
            content = (
                "# Legal Research Memorandum\n\n"
                f"The memorandum could not be generated due to an error: {e}. "
                "Please retry. No legal conclusions should be drawn from this message."
            )

    return {"final_report": content}

# ===== GRAPH CONSTRUCTION =====
# Build the overall workflow
deep_researcher_builder = StateGraph(AgentState, input_schema=AgentInputState)

# Add workflow nodes
deep_researcher_builder.add_node("load_memory", load_memory)
deep_researcher_builder.add_node("compact_conversation", compact_conversation)
deep_researcher_builder.add_node("clarify_with_user", clarify_with_user)
deep_researcher_builder.add_node("write_research_brief", write_research_brief)
deep_researcher_builder.add_node("bootstrap_research", bootstrap_research)
deep_researcher_builder.add_node("supervisor_subgraph", supervisor_agent)
deep_researcher_builder.add_node("final_report_generation", final_report_generation)
deep_researcher_builder.add_node("verify_report", verify_report)
deep_researcher_builder.add_node("finalize_report", finalize_report)

# Add workflow edges
# START -> load_memory (inject long-term + conversation memory, persist turn)
# -> clarify_with_user -> write_research_brief -> supervisor -> draft report
# -> verify_report -> (revise via final_report_generation | finalize_report) -> END.
deep_researcher_builder.add_edge(START, "load_memory")
deep_researcher_builder.add_edge("load_memory", "compact_conversation")
deep_researcher_builder.add_edge("compact_conversation", "clarify_with_user")
deep_researcher_builder.add_edge("write_research_brief", "bootstrap_research")
deep_researcher_builder.add_conditional_edges(
    "bootstrap_research",
    route_after_bootstrap,
    {
        "supervisor_subgraph": "supervisor_subgraph",
        "final_report_generation": "final_report_generation",
    },
)
deep_researcher_builder.add_edge("supervisor_subgraph", "final_report_generation")
deep_researcher_builder.add_edge("final_report_generation", "verify_report")
deep_researcher_builder.add_conditional_edges(
    "verify_report",
    route_after_verify,
    {
        "final_report_generation": "final_report_generation",  # revise with feedback
        "finalize_report": "finalize_report",  # ship (passed, or retries exhausted -> caveats)
    },
)
deep_researcher_builder.add_edge("finalize_report", END)

# Compile the full workflow
agent = deep_researcher_builder.compile()
