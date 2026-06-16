
"""Multi-agent supervisor for coordinating research across multiple specialized agents.

This module implements a supervisor pattern where:
1. A supervisor agent coordinates research activities and delegates tasks
2. Multiple researcher agents work on specific sub-topics independently
3. Results are aggregated and compressed for final reporting

The supervisor uses parallel research execution to improve efficiency while
maintaining isolated context windows for each research topic.
"""

import asyncio

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    filter_messages,
)
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from typing_extensions import Literal

from deep_research_from_scratch.legal_think_tool import think_tool
from deep_research_from_scratch.memory_tools import compact_message_list
from deep_research_from_scratch.model_config import (
    ainvoke_with_retry,
    cap_max_tokens_for_prompt,
    get_chat_model,
)
from deep_research_from_scratch.prompts import lead_researcher_prompt
from deep_research_from_scratch.research_agent import researcher_agent
from deep_research_from_scratch.state_multi_agent_supervisor import (
    ConductResearch,
    ResearchComplete,
    SupervisorState,
)
from deep_research_from_scratch.utils import get_today_str


def get_notes_from_tool_calls(messages: list[BaseMessage]) -> list[str]:
    """Extract research notes from ToolMessage objects in supervisor message history.

    This function retrieves the compressed research findings that sub-agents
    return as ToolMessage content. When the supervisor delegates research to
    sub-agents via ConductResearch tool calls, each sub-agent returns its
    compressed findings as the content of a ToolMessage. This function
    extracts all such ToolMessage content to compile the final research notes.

    Args:
        messages: List of messages from supervisor's conversation history

    Returns:
        List of research note strings extracted from ToolMessage objects
    """
    return [tool_msg.content for tool_msg in filter_messages(messages, include_types="tool")]

# Ensure async compatibility for Jupyter environments
try:
    import nest_asyncio
    # Only apply if running in Jupyter/IPython environment
    try:
        from IPython import get_ipython
        if get_ipython() is not None:
            nest_asyncio.apply()
    except ImportError:
        pass  # Not in Jupyter, no need for nest_asyncio
except ImportError:
    pass  # nest_asyncio not available, proceed without it


# ===== CONFIGURATION =====

from deep_research_from_scratch.config import config

# Tool LIST (named distinctly from the supervisor_tools NODE function below to
# avoid the name shadowing the list once the function is defined).
SUPERVISOR_TOOLS = [ConductResearch, ResearchComplete, think_tool]
supervisor_model = get_chat_model("supervisor")
supervisor_model_with_tools = supervisor_model.bind_tools(SUPERVISOR_TOOLS)

# System constants (centralized in config.py)
# Maximum number of tool call iterations for individual researcher agents;
# prevents infinite loops and controls research depth per topic.
max_researcher_iterations = config.MAX_RESEARCHER_ITERATIONS

# Maximum number of concurrent research agents the supervisor can launch
# (passed to the lead_researcher_prompt to limit parallel research tasks).
max_concurrent_researchers = config.MAX_CONCURRENT_RESEARCHERS

# ===== SUPERVISOR NODES =====

def compact_supervisor_context(state: SupervisorState) -> dict:
    """Compact the supervisor's coordination context when it grows too long.

    Runs before each supervisor decision. The compressed research findings are
    already preserved durably in ``notes`` state (see ``supervisor_tools``), so it
    is safe to summarize older coordination messages here without losing the
    deliverable. Tool-call/result pairs are kept intact. No-op for short loops.
    """
    update = compact_message_list(
        list(state.get("supervisor_messages", [])),
        summary_label="Earlier coordination + findings (summarized to save context)",
    )
    return {"supervisor_messages": update} if update else {}

async def supervisor(state: SupervisorState) -> Command[Literal["supervisor_tools"]]:
    """Coordinate research activities.

    Analyzes the research brief and current progress to decide:
    - What research topics need investigation
    - Whether to conduct parallel research
    - When research is complete

    Args:
        state: Current supervisor state with messages and research progress

    Returns:
        Command to proceed to supervisor_tools node with updated state
    """
    supervisor_messages = state.get("supervisor_messages", [])

    # Prepare system message with current date and constraints
    system_message = lead_researcher_prompt.format(
        date=get_today_str(), 
        max_concurrent_research_units=max_concurrent_researchers,
        max_researcher_iterations=max_researcher_iterations
    )
    messages = [SystemMessage(content=system_message)] + supervisor_messages
    prompt_text = "\n".join(str(getattr(m, "content", m)) for m in messages)

    # Make decision about next research steps. Degrade gracefully on error: a
    # response with no tool_calls makes supervisor_tools end cleanly with the
    # notes gathered so far, rather than crashing the whole run.
    try:
        safe_max_tokens = cap_max_tokens_for_prompt(
            prompt_text,
            role="supervisor",
            requested_max_tokens=8192,
        )
        model = (
            supervisor_model_with_tools.bind(max_tokens=safe_max_tokens)
            if safe_max_tokens is not None
            else supervisor_model_with_tools
        )
        response = await ainvoke_with_retry(model, messages)
    except Exception as e:  # noqa: BLE001
        response = AIMessage(content=f"Supervisor step failed: {e}. Concluding research with findings gathered so far.")

    return Command(
        goto="supervisor_tools",
        update={
            "supervisor_messages": [response],
            "research_iterations": state.get("research_iterations", 0) + 1
        }
    )

async def supervisor_tools(state: SupervisorState) -> Command[Literal["compact_supervisor_context", "__end__"]]:
    """Execute supervisor decisions - either conduct research or end the process.

    Handles:
    - Executing think_tool calls for strategic reflection
    - Launching parallel research agents for different topics
    - Aggregating research results
    - Determining when research is complete

    Args:
        state: Current supervisor state with messages and iteration count

    Returns:
        Command to continue supervision, end process, or handle errors
    """
    supervisor_messages = state.get("supervisor_messages", [])
    research_iterations = state.get("research_iterations", 0)
    most_recent_message = supervisor_messages[-1]

    # Initialize variables for single return pattern
    tool_messages = []
    all_raw_notes = []
    all_sources = []
    new_notes = []  # Compressed findings accumulated durably into state.
    next_step = "compact_supervisor_context"  # Default next step
    should_end = False

    # Check exit criteria first
    exceeded_iterations = research_iterations >= max_researcher_iterations
    no_tool_calls = not most_recent_message.tool_calls
    research_complete = any(
        tool_call["name"] == "ResearchComplete" 
        for tool_call in most_recent_message.tool_calls
    )

    if exceeded_iterations or no_tool_calls or research_complete:
        should_end = True
        next_step = END

    else:
        # Execute ALL tool calls before deciding next step
        try:
            # Separate think_tool calls from ConductResearch calls
            think_tool_calls = [
                tool_call for tool_call in most_recent_message.tool_calls 
                if tool_call["name"] == "think_tool"
            ]

            conduct_research_calls = [
                tool_call for tool_call in most_recent_message.tool_calls 
                if tool_call["name"] == "ConductResearch"
            ]

            # Handle think_tool calls (synchronous)
            for tool_call in think_tool_calls:
                observation = think_tool.invoke(tool_call["args"])
                tool_messages.append(
                    ToolMessage(
                        content=observation,
                        name=tool_call["name"],
                        tool_call_id=tool_call["id"]
                    )
                )

            # Handle ConductResearch calls (asynchronous)
            if conduct_research_calls:
                bootstrap_notes = "\n".join(state.get("notes") or [])[:4000]
                seed_sources = state.get("retrieved_sources") or []
                seed_fetches = sum(
                    1 for s in seed_sources if getattr(s, "fetched", False)
                )
                seed_searches = len(seed_sources)

                coros = []
                for tool_call in conduct_research_calls:
                    topic = tool_call["args"]["research_topic"]
                    content = topic
                    if bootstrap_notes:
                        content += (
                            "\n\n--- Prior bootstrap findings (already retrieved; "
                            "build on these, fetch any missing primary sources) ---\n"
                            + bootstrap_notes
                        )
                    coros.append(
                        researcher_agent.ainvoke({
                            "researcher_messages": [HumanMessage(content=content)],
                            "research_topic": topic,
                            "retrieved_sources": seed_sources,
                            "fetch_count": seed_fetches,
                            "search_count": seed_searches,
                            "fetch_gate_retries": 0,
                            "tool_call_iterations": 0,
                        })
                    )

                # Wait for all research to complete
                tool_results = await asyncio.gather(*coros)

                # Format research results as tool messages
                # Each sub-agent returns compressed research findings in result["compressed_research"]
                # We write this compressed research as the content of a ToolMessage, which allows
                # the supervisor to later retrieve these findings via get_notes_from_tool_calls()
                research_tool_messages = [
                    ToolMessage(
                        content=result.get("compressed_research", "Error synthesizing research report"),
                        name=tool_call["name"],
                        tool_call_id=tool_call["id"]
                    ) for result, tool_call in zip(tool_results, conduct_research_calls)
                ]

                tool_messages.extend(research_tool_messages)

                # Persist the compressed findings durably into `notes` NOW, so the
                # final report does not depend on these ToolMessages surviving in
                # supervisor_messages (which may be compacted on later iterations).
                new_notes = [tm.content for tm in research_tool_messages]

                # Aggregate raw notes and structured sources from all research
                all_raw_notes = [
                    "\n".join(result.get("raw_notes", []))
                    for result in tool_results
                ]
                all_sources: list = []
                for result in tool_results:
                    all_sources.extend(result.get("retrieved_sources") or [])

        except Exception as e:
            print(f"Error in supervisor tools: {e}")
            should_end = True
            next_step = END

    # Single return point with appropriate state updates
    if should_end:
        # Findings were accumulated into `notes` as research completed. Fall back
        # to extracting from the message history only if nothing was accumulated
        # (e.g. research completed in a single pass without prior aggregation).
        end_update = {"research_brief": state.get("research_brief", "")}
        if not state.get("notes"):
            end_update["notes"] = get_notes_from_tool_calls(supervisor_messages)
        return Command(goto=next_step, update=end_update)
    else:
        return Command(
            goto=next_step,
            update={
                "supervisor_messages": tool_messages,
                "notes": new_notes,
                "raw_notes": all_raw_notes,
                "retrieved_sources": all_sources,
            }
        )

# ===== GRAPH CONSTRUCTION =====

# Build supervisor graph
# Flow: START -> compact_supervisor_context -> supervisor -> supervisor_tools
#       -> (compact_supervisor_context | END). Compaction runs before each
#       supervisor decision so a long coordination loop stays within token limits.
supervisor_builder = StateGraph(SupervisorState)
supervisor_builder.add_node("compact_supervisor_context", compact_supervisor_context)
supervisor_builder.add_node("supervisor", supervisor)
supervisor_builder.add_node("supervisor_tools", supervisor_tools)
supervisor_builder.add_edge(START, "compact_supervisor_context")
supervisor_builder.add_edge("compact_supervisor_context", "supervisor")
supervisor_agent = supervisor_builder.compile()
