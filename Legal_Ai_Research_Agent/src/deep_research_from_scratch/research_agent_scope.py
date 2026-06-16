
"""User Clarification and Research Brief Generation.

This module implements the scoping phase of the research workflow, where we:
1. Assess if the user's request needs clarification
2. Generate a detailed research brief from the conversation

The workflow uses structured output to make deterministic decisions about
whether sufficient context exists to proceed with research.
"""

from datetime import datetime

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    get_buffer_string,
)
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from typing_extensions import Literal

from deep_research_from_scratch.config import config as app_config
from deep_research_from_scratch.memory_backend import format_hits, get_memory_backend
from deep_research_from_scratch.memory_tools import (
    build_session_context,
    compact_conversation,
    get_session_id,
    load_memory_prompt,
    record_transcript,
)
from deep_research_from_scratch.model_config import get_chat_model, invoke_with_retry
from deep_research_from_scratch.retrieval_bridge import set_request_context
from deep_research_from_scratch.prompts import (
    suggest_directions_prompt,
    transform_messages_into_research_topic_prompt,
)
from deep_research_from_scratch.state_scope import (
    AgentInputState,
    AgentState,
    SuggestDirections,
    ResearchQuestion,
)

# Marker prefix identifying the memory block this node injects, so stale blocks
# can be removed (deduped) on subsequent turns instead of accumulating.
MEMORY_BLOCK_PREFIX = "## Persistent memory (for your awareness)"

# ===== UTILITY FUNCTIONS =====

def get_today_str() -> str:
    """Get current date in a human-readable format."""
    now = datetime.now()
    return f"{now.strftime('%a %b')} {now.day}, {now.year}"

# ===== CONFIGURATION =====

# Initialize model (routed through central config for on-prem support)
model = get_chat_model("reasoning", temperature=0.0)

# ===== WORKFLOW NODES =====

def load_memory(state: AgentState, config: RunnableConfig) -> dict:
    """Inject memory at the start of the turn (the QueryEngine.ts / loadMemoryPrompt step).

    Hybrid recall designed for continuous, long conversations without losing
    context (and bounded in size, so it stays fast):
    1. Long-term memory index (MEMORY.md) + cross-session facts relevant to the
       request, via the pluggable memory backend (keyword now, vector-ready).
    2. A rolling per-session summary of older turns + the most recent turns
       verbatim (full transcript stays on disk; injection size is capped).
    3. Other relevant earlier turns retrieved by the query (catches context
       outside the recent window).
    4. Persists the latest message to the transcript and dedupes any stale memory
       block from a prior turn before injecting the fresh one.
    """
    session_id = get_session_id(config)
    tenant_id = (config.get("configurable") or {}).get("tenant_id")
    set_request_context(tenant_id=tenant_id)
    messages = state.get("messages", [])

    # The latest user message drives retrieval (built BEFORE we record it, so it
    # is not duplicated inside the recalled conversation context).
    latest_user_text = ""
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage) and isinstance(msg.content, str):
            latest_user_text = msg.content
            break

    # Input validation: cap pathologically long input before it drives retrieval
    # / summarization, so a single huge message cannot blow up cost or context.
    if latest_user_text and len(latest_user_text) > app_config.MAX_INPUT_CHARS:
        latest_user_text = latest_user_text[: app_config.MAX_INPUT_CHARS]

    backend = get_memory_backend()

    # 1. Long-term memory: index + cross-session facts relevant to this request
    #    (keyword today, semantic when a vector backend is configured).
    memory_index = load_memory_prompt()
    longterm_hits = backend.search_longterm(latest_user_text, k=5) if latest_user_text else []
    recalled = format_hits(longterm_hits, empty="No long-term memories matched this request.")

    # 2. Bounded conversation context: rolling summary of older turns + recent
    #    turns verbatim. Stays small regardless of how long the session gets.
    session_ctx = build_session_context(session_id)

    # 3. Other relevant earlier turns retrieved by the query (catches context
    #    that fell outside the recent window). Vector-ready via the backend seam.
    session_hits = backend.search_session(session_id, latest_user_text, k=3) if latest_user_text else []
    relevant_older = format_hits(session_hits, empty="None.")

    memory_block = (
        f"{MEMORY_BLOCK_PREFIX}\n"
        f"{memory_index}\n\n"
        f"### Recalled long-term facts relevant to this request\n{recalled}\n\n"
        f"### Conversation so far (this session)\n{session_ctx}\n\n"
        f"### Other relevant earlier turns\n{relevant_older}"
    )

    # Now persist the newest message to the transcript (after building context).
    if messages:
        last = messages[-1]
        content = getattr(last, "content", "")
        if isinstance(content, str) and content.strip():
            role = "user" if isinstance(last, HumanMessage) else "assistant"
            record_transcript(session_id, role, content)

    # Dedupe: drop any stale memory block injected on a previous turn so the
    # context does not accumulate duplicate memory blocks over a long thread.
    removals = [
        RemoveMessage(id=m.id)
        for m in messages
        if isinstance(m, SystemMessage)
        and isinstance(getattr(m, "content", None), str)
        and m.content.startswith(MEMORY_BLOCK_PREFIX)
        and getattr(m, "id", None) is not None
    ]

    return {"messages": [*removals, SystemMessage(content=memory_block)]}

def clarify_with_user(state: AgentState, config: RunnableConfig) -> Command[Literal["write_research_brief", "__end__"]]:
    """Suggest research directions or ask a targeted clarifying question before starting research.

    Three possible actions:
    - suggest_directions: present 3-4 research angles for user to choose; graph pauses.
    - ask_clarification: ask ONE missing-fact question; graph pauses.
    - proceed: start research immediately.
    """
    if not app_config.ALLOW_CLARIFICATION:
        return Command(
            goto="write_research_brief",
            update={
                "messages": [AIMessage(content="Proceeding with research based on the information provided.")],
                "research_directions": [],
            },
        )

    structured_output_model = model.with_structured_output(SuggestDirections)

    response = structured_output_model.invoke([
        HumanMessage(content=suggest_directions_prompt.format(
            messages=get_buffer_string(messages=state["messages"]),
            date=get_today_str(),
        ))
    ])

    session_id = get_session_id(config)

    if response.action == "suggest_directions":
        directions_list = "\n".join(
            f"{i + 1}. {d}" for i, d in enumerate(response.research_directions)
        )
        text = f"{response.direction_context}\n\n{directions_list}"
        record_transcript(session_id, "assistant", text)
        return Command(
            goto=END,
            update={
                "messages": [AIMessage(content=text)],
                "research_directions": response.research_directions,
            },
        )

    if response.action == "ask_clarification":
        record_transcript(session_id, "assistant", response.clarification_question)
        return Command(
            goto=END,
            update={
                "messages": [AIMessage(content=response.clarification_question)],
                "research_directions": [],
            },
        )

    # action == "proceed"
    record_transcript(session_id, "assistant", response.verification)
    return Command(
        goto="write_research_brief",
        update={
            "messages": [AIMessage(content=response.verification)],
            "research_directions": [],
        },
    )

def write_research_brief(state: AgentState, config: RunnableConfig):
    """Transform the conversation history into a comprehensive research brief.

    Uses structured output to ensure the brief follows the required format
    and contains all necessary details for effective research.
    """
    # Set up structured output model
    structured_output_model = model.with_structured_output(ResearchQuestion)

    # Generate research brief from conversation history
    response = invoke_with_retry(
        structured_output_model,
        [
            HumanMessage(
                content=transform_messages_into_research_topic_prompt.format(
                    messages=get_buffer_string(state.get("messages", [])),
                    date=get_today_str(),
                )
            )
        ],
    )

    # Persist the generated brief to the session transcript.
    record_transcript(get_session_id(config), "assistant", response.research_brief)

    # Update state with generated research brief and pass it to the supervisor
    return {
        "research_brief": response.research_brief,
        "supervisor_messages": [HumanMessage(content=f"{response.research_brief}.")]
    }

# ===== GRAPH CONSTRUCTION =====

# Build the scoping workflow
deep_researcher_builder = StateGraph(AgentState, input_schema=AgentInputState)

# Add workflow nodes
deep_researcher_builder.add_node("load_memory", load_memory)
deep_researcher_builder.add_node("compact_conversation", compact_conversation)
deep_researcher_builder.add_node("clarify_with_user", clarify_with_user)
deep_researcher_builder.add_node("write_research_brief", write_research_brief)

# Add workflow edges
# START -> load_memory (inject memory + persist) -> compact_conversation
# (summarize if the chat is long) -> clarify_with_user (routes onward)
deep_researcher_builder.add_edge(START, "load_memory")
deep_researcher_builder.add_edge("load_memory", "compact_conversation")
deep_researcher_builder.add_edge("compact_conversation", "clarify_with_user")
deep_researcher_builder.add_edge("write_research_brief", END)

# Compile the workflow
scope_research = deep_researcher_builder.compile()
