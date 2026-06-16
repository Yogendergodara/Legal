
"""Research Agent Implementation.

This module implements a research agent that can perform iterative web searches
and synthesis to answer complex research questions.
"""

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    filter_messages,
)
from langgraph.graph import END, START, StateGraph
from typing_extensions import Literal

from deep_research_from_scratch.config import config as app_config
from deep_research_from_scratch.legal_think_tool import think_tool
from deep_research_from_scratch.memory_mcp_tools import save_memory, search_memory
from deep_research_from_scratch.memory_tools import compact_message_list
from deep_research_from_scratch.model_config import (
    cap_max_tokens_for_prompt,
    get_chat_model,
    invoke_with_retry,
)
from deep_research_from_scratch.prompts import (
    compress_research_human_message,
    compress_research_system_prompt,
    research_agent_prompt,
)
from deep_research_from_scratch.retrieval_bridge import run_fetch, run_search, run_semantic_search
from deep_research_from_scratch.search_tools import fetch_url, semantic_search, web_search
from deep_research_from_scratch.source_registry import (
    RetrievedSource,
    count_fetches,
    has_primary_search_urls,
)
from deep_research_from_scratch.state_research import (
    ResearcherOutputState,
    ResearcherState,
)
from deep_research_from_scratch.utils import get_today_str

# ===== CONFIGURATION =====

tools = [web_search, semantic_search, fetch_url, think_tool, search_memory, save_memory]
tools_by_name = {tool.name: tool for tool in tools}

model = get_chat_model("researcher")
model_with_tools = model.bind_tools(tools)
summarization_model = get_chat_model("summarizer")
compress_model = get_chat_model("compress", max_tokens=32000)

_FETCH_REMINDER = (
    "MANDATORY: You have not yet fetched enough primary legal sources. "
    "Use fetch_url on indiankanoon.org, indiacode.nic.in, digiscr.sci.gov.in, "
    "or other .gov.in court URLs from your search results BEFORE finishing. "
    "Snippets alone are NOT citable. If no primary source exists after thorough "
    "search, state explicitly that the point was NOT FOUND."
)


def _execute_retrieval_tool(
    name: str, args: dict
) -> tuple[str, list[RetrievedSource], dict[str, int]]:
    """Run retrieval tools and capture structured source updates."""
    counter_updates: dict[str, int] = {}
    if name == "web_search":
        text, sources = run_search(args["query"], int(args.get("max_results", 5)))
        counter_updates["search_count"] = 1
        return text, sources, counter_updates
    if name == "semantic_search":
        text, sources = run_semantic_search(args["query"], int(args.get("max_results", 5)))
        counter_updates["search_count"] = 1
        return text, sources, counter_updates
    if name == "fetch_url":
        text, src = run_fetch(str(args["url"]))
        sources = [src] if src else []
        if src:
            counter_updates["fetch_count"] = 1
        return text, sources, counter_updates
    tool = tools_by_name[name]
    return str(tool.invoke(args)), [], {}


def fetch_gate_passed(state: ResearcherState) -> bool:
    """Return True when minimum fetch discipline is satisfied."""
    sources = state.get("retrieved_sources") or []
    fetch_total, primary_fetches = count_fetches(sources)
    search_count = state.get("search_count", 0)

    if fetch_total >= app_config.MIN_FETCHES:
        if has_primary_search_urls(sources) and primary_fetches < app_config.MIN_PRIMARY_FETCHES:
            return False
        return True

    last_message = state["researcher_messages"][-1]
    last_content = str(getattr(last_message, "content", "") or "").lower()
    if fetch_total >= 1 and (
        "not found" in last_content or "no primary source" in last_content
    ):
        return True

    if (
        state.get("fetch_gate_retries", 0) >= app_config.MAX_FETCH_GATE_RETRIES
        and fetch_total >= 1
    ):
        return True

    if (
        state.get("fetch_gate_retries", 0) >= app_config.MAX_FETCH_GATE_RETRIES
        and search_count >= app_config.MIN_SEARCHES
        and fetch_total == 0
    ):
        return True

    return False


# ===== AGENT NODES =====

def llm_call(state: ResearcherState):
    """Analyze current state and decide on next actions."""
    messages = (
        [SystemMessage(content=research_agent_prompt.format(date=get_today_str()))]
        + state["researcher_messages"]
    )
    prompt_text = "\n".join(str(getattr(m, "content", m)) for m in messages)
    try:
        safe_max_tokens = cap_max_tokens_for_prompt(
            prompt_text,
            role="researcher",
            requested_max_tokens=8192,
        )
        model = (
            model_with_tools.bind(max_tokens=safe_max_tokens)
            if safe_max_tokens is not None
            else model_with_tools
        )
        response = invoke_with_retry(model, messages)
    except Exception as e:  # noqa: BLE001
        response = AIMessage(
            content=f"Research step could not be completed due to an error: {e}. "
            "Proceeding with the information gathered so far."
        )
    return {
        "researcher_messages": [response],
        "tool_call_iterations": state.get("tool_call_iterations", 0) + 1,
    }


def compact_research_context(state: ResearcherState) -> dict:
    """Compact the researcher's context window when it grows too long."""
    update = compact_message_list(
        list(state.get("researcher_messages", [])),
        summary_label="Earlier research in this task (summarized to save context)",
    )
    return {"researcher_messages": update} if update else {}


def tool_node(state: ResearcherState):
    """Execute tool calls and accumulate structured source registry updates."""
    tool_calls = state["researcher_messages"][-1].tool_calls

    observations: list[str] = []
    new_sources: list[RetrievedSource] = []
    search_delta = 0
    fetch_delta = 0

    for tool_call in tool_calls:
        text, sources, counters = _execute_retrieval_tool(tool_call["name"], tool_call["args"])
        observations.append(text)
        new_sources.extend(sources)
        search_delta += counters.get("search_count", 0)
        fetch_delta += counters.get("fetch_count", 0)

    tool_outputs = [
        ToolMessage(
            content=observation,
            name=tool_call["name"],
            tool_call_id=tool_call["id"],
        )
        for observation, tool_call in zip(observations, tool_calls)
    ]

    update: dict = {"researcher_messages": tool_outputs}
    if new_sources:
        update["retrieved_sources"] = new_sources
    if search_delta:
        update["search_count"] = state.get("search_count", 0) + search_delta
    if fetch_delta:
        update["fetch_count"] = state.get("fetch_count", 0) + fetch_delta
    return update


def fetch_reminder(state: ResearcherState) -> dict:
    """Inject a reminder when the agent tries to finish without enough fetches."""
    return {
        "researcher_messages": [SystemMessage(content=_FETCH_REMINDER)],
        "fetch_gate_retries": state.get("fetch_gate_retries", 0) + 1,
    }


def compress_research(state: ResearcherState) -> dict:
    """Compress research findings into a concise summary."""
    system_message = compress_research_system_prompt.format(date=get_today_str())
    formatted_human = compress_research_human_message.format(
        research_topic=state.get("research_topic", "")
    )
    messages = (
        [SystemMessage(content=system_message)]
        + state.get("researcher_messages", [])
        + [HumanMessage(content=formatted_human)]
    )
    prompt_text = "\n".join(str(m.content) for m in messages)
    safe_max_tokens = cap_max_tokens_for_prompt(
        prompt_text,
        role="compress",
        requested_max_tokens=32000,
    )
    model = (
        compress_model.bind(max_tokens=safe_max_tokens)
        if safe_max_tokens is not None
        else compress_model
    )
    response = invoke_with_retry(model, messages)

    raw_notes = [
        str(m.content)
        for m in filter_messages(state["researcher_messages"], include_types=["tool", "ai"])
    ]

    result: dict = {
        "compressed_research": str(response.content),
        "raw_notes": ["\n".join(raw_notes)],
    }
    if state.get("retrieved_sources"):
        result["retrieved_sources"] = state["retrieved_sources"]
    if state.get("fetch_count"):
        result["fetch_count"] = state["fetch_count"]
    if state.get("search_count"):
        result["search_count"] = state["search_count"]
    return result


# ===== ROUTING LOGIC =====

def should_continue(
    state: ResearcherState,
) -> Literal["tool_node", "compress_research", "fetch_reminder"]:
    """Route to tools, compression, or fetch reminder."""
    last_message = state["researcher_messages"][-1]
    if last_message.tool_calls:
        return "tool_node"
    if fetch_gate_passed(state):
        return "compress_research"
    return "fetch_reminder"


# ===== GRAPH CONSTRUCTION =====

agent_builder = StateGraph(ResearcherState, output_schema=ResearcherOutputState)

agent_builder.add_node("compact_research_context", compact_research_context)
agent_builder.add_node("llm_call", llm_call)
agent_builder.add_node("tool_node", tool_node)
agent_builder.add_node("fetch_reminder", fetch_reminder)
agent_builder.add_node("compress_research", compress_research)

agent_builder.add_edge(START, "compact_research_context")
agent_builder.add_edge("compact_research_context", "llm_call")
agent_builder.add_conditional_edges(
    "llm_call",
    should_continue,
    {
        "tool_node": "tool_node",
        "compress_research": "compress_research",
        "fetch_reminder": "fetch_reminder",
    },
)
agent_builder.add_edge("tool_node", "compact_research_context")
agent_builder.add_edge("fetch_reminder", "compact_research_context")
agent_builder.add_edge("compress_research", END)

researcher_agent = agent_builder.compile()
