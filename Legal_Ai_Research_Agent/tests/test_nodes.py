"""Tests for scoping, supervisor, compaction, and report drafting nodes, including graceful degradation try-except blocks."""

from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
)
from langgraph.types import Command

from deep_research_from_scratch.legal_think_tool import think_tool
from deep_research_from_scratch.multi_agent_supervisor import (
    compact_supervisor_context,
    supervisor,
    supervisor_tools,
)
from deep_research_from_scratch.prompts import compress_research_human_message
from deep_research_from_scratch.research_agent import compact_research_context, llm_call
from deep_research_from_scratch.research_agent_full import final_report_generation

# Import nodes to test
from deep_research_from_scratch.research_agent_scope import (
    clarify_with_user,
    load_memory,
)
from deep_research_from_scratch.state_multi_agent_supervisor import SupervisorState
from deep_research_from_scratch.state_scope import AgentState, SuggestDirections

# ===== 1. GRACEFUL DEGRADATION TESTS =====

def test_llm_call_degrades_gracefully():
    """Verify llm_call node handles model exception and returns warning message."""
    state = {"researcher_messages": [HumanMessage(content="test")]}
    
    with patch("deep_research_from_scratch.research_agent.model_with_tools.invoke") as mock_invoke:
        mock_invoke.side_effect = Exception("LLM connection refused")
        
        update = llm_call(state)
        assert len(update["researcher_messages"]) == 1
        assert "could not be completed due to an error" in update["researcher_messages"][0].content
        assert "LLM connection refused" in update["researcher_messages"][0].content


@pytest.mark.asyncio
async def test_supervisor_degrades_gracefully():
    """Verify supervisor node handles model exception and proceeds to supervisor_tools."""
    state = SupervisorState(
        supervisor_messages=[HumanMessage(content="test")],
        research_brief="test brief",
        notes=[],
        raw_notes=[],
        research_iterations=0,
    )
    
    with patch("deep_research_from_scratch.multi_agent_supervisor.supervisor_model_with_tools.ainvoke") as mock_ainvoke:
        mock_ainvoke.side_effect = Exception("Anthropic API rate limit")
        
        cmd = await supervisor(state)
        assert isinstance(cmd, Command)
        assert cmd.goto == "supervisor_tools"
        assert "Supervisor step failed" in cmd.update["supervisor_messages"][0].content
        assert "Anthropic API rate limit" in cmd.update["supervisor_messages"][0].content
        assert cmd.update["research_iterations"] == 1


@pytest.mark.asyncio
async def test_final_report_generation_degrades_gracefully():
    """Verify final_report_generation node handles model exception and returns fallback report."""
    state = AgentState(
        final_report=None,
        notes=["Found fact 1"],
        research_brief="brief",
        verification_retries=0,
    )
    
    with patch("deep_research_from_scratch.research_agent_full.writer_model.ainvoke") as mock_ainvoke:
        mock_ainvoke.side_effect = Exception("Context window exceeded")
        
        update = await final_report_generation(state, config={"configurable": {"thread_id": "sess_writer"}})
        assert "final_report" in update
        assert "could not be generated due to an error" in update["final_report"]
        assert "Context window exceeded" in update["final_report"]


# ===== 2. HYBRID LOAD_MEMORY RECALL & DEDUPLICATION =====

def test_load_memory_hybrid_recall(configure_test_memory_dir):
    """Verify load_memory retrieves backend hits, rolling session, and deduplicates older memory blocks."""
    state = AgentState(
        messages=[
            SystemMessage(content="## Persistent memory (for your awareness)\nSome old block", id="old_block_id"),
            HumanMessage(content="What is the rule under Section 27?", id="user_msg_id"),
        ]
    )
    
    # Mock search_longterm to return hits
    from deep_research_from_scratch.memory_backend import MemoryHit
    mock_hit = MemoryHit(text="Section 27 is void", source="contract_act.md")
    
    with patch("deep_research_from_scratch.research_agent_scope.get_memory_backend") as mock_get_backend:
        mock_backend = MagicMock()
        mock_backend.search_longterm.return_value = [mock_hit]
        mock_backend.search_session.return_value = []
        mock_get_backend.return_value = mock_backend
        
        update = load_memory(state, config={"configurable": {"thread_id": "session_hybrid"}})
        
        # Check that it returned a RemoveMessage for the old memory block
        removals = [m for m in update["messages"] if isinstance(m, RemoveMessage)]
        assert len(removals) == 1
        assert removals[0].id == "old_block_id"
        
        # Check that it appended a new SystemMessage with the memory block prefix
        system_msgs = [m for m in update["messages"] if isinstance(m, SystemMessage)]
        assert len(system_msgs) == 1
        assert system_msgs[0].content.startswith("## Persistent memory (for your awareness)")
        assert "contract_act.md" in system_msgs[0].content
        assert "Section 27 is void" in system_msgs[0].content


# ===== 3. SCOPING DECISION ROUTING =====

def test_clarify_with_user_bypass_when_disabled():
    """When ALLOW_CLARIFICATION is false, skip LLM and proceed to write_research_brief."""
    state = AgentState(messages=[HumanMessage(content="help")])

    with patch("deep_research_from_scratch.research_agent_scope.app_config.ALLOW_CLARIFICATION", False):
        cmd = clarify_with_user(state, config={"configurable": {"thread_id": "s1"}})
        assert cmd.goto == "write_research_brief"
        assert len(cmd.update["messages"]) == 1
        assert "Proceeding with research" in cmd.update["messages"][0].content


def test_clarify_with_user_routing():
    """Verify clarify_with_user routes to END for directions/clarification and write_research_brief on proceed."""
    state = AgentState(messages=[HumanMessage(content="help")])

    # Scenario 1: Suggest research directions (default path)
    mock_directions = SuggestDirections(
        action="suggest_directions",
        research_directions=[
            "BNS bail under Section 480 — post-July 2024 SC rulings",
            "Anticipatory bail under CrPC Section 438",
        ],
        direction_context="I can research this from these angles:",
        clarification_question="",
        verification="",
    )

    with patch("deep_research_from_scratch.research_agent_scope.app_config.ALLOW_CLARIFICATION", True):
        with patch("deep_research_from_scratch.research_agent_scope.model.with_structured_output") as mock_with_struct:
            mock_with_struct.return_value.invoke.return_value = mock_directions

            cmd = clarify_with_user(state, config={"configurable": {"thread_id": "s1"}})
            assert cmd.goto == "__end__"
            assert len(cmd.update["research_directions"]) == 2
            assert "BNS bail" in cmd.update["messages"][0].content

    # Scenario 2: Ask a targeted clarification question
    mock_ask = SuggestDirections(
        action="ask_clarification",
        research_directions=[],
        direction_context="",
        clarification_question="What was the date of the alleged offence?",
        verification="",
    )
    with patch("deep_research_from_scratch.research_agent_scope.app_config.ALLOW_CLARIFICATION", True):
        with patch("deep_research_from_scratch.research_agent_scope.model.with_structured_output") as mock_with_struct:
            mock_with_struct.return_value.invoke.return_value = mock_ask

            cmd = clarify_with_user(state, config={"configurable": {"thread_id": "s1"}})
            assert cmd.goto == "__end__"
            assert cmd.update["messages"][0].content == "What was the date of the alleged offence?"

    # Scenario 3: User already selected a direction — proceed to research
    mock_proceed = SuggestDirections(
        action="proceed",
        research_directions=[],
        direction_context="",
        clarification_question="",
        verification="Proceeding under Supreme Court rules.",
    )
    with patch("deep_research_from_scratch.research_agent_scope.app_config.ALLOW_CLARIFICATION", True):
        with patch("deep_research_from_scratch.research_agent_scope.model.with_structured_output") as mock_with_struct:
            mock_with_struct.return_value.invoke.return_value = mock_proceed

            cmd = clarify_with_user(state, config={"configurable": {"thread_id": "s1"}})
            assert cmd.goto == "write_research_brief"
            assert cmd.update["messages"][0].content == "Proceeding under Supreme Court rules."


# ===== 4. COMPACTION NODE WRAPPERS =====

def test_graph_compaction_nodes():
    """Verify graph context compaction nodes return correct updates."""
    # Researcher compaction
    state_res = {"researcher_messages": [HumanMessage(content="msg", id="1")] * 20}
    with patch("deep_research_from_scratch.research_agent.compact_message_list") as mock_compact:
        mock_compact.return_value = [RemoveMessage(id="1"), SystemMessage(content="summary")]
        update = compact_research_context(state_res)
        assert "researcher_messages" in update
        assert len(update["researcher_messages"]) == 2

    # Supervisor compaction
    state_sup = SupervisorState(
        supervisor_messages=[HumanMessage(content="msg", id="2")] * 20,
        research_brief="brief",
        notes=[],
        raw_notes=[],
        research_iterations=1,
    )
    with patch("deep_research_from_scratch.multi_agent_supervisor.compact_message_list") as mock_compact:
        mock_compact.return_value = [RemoveMessage(id="2"), SystemMessage(content="summary")]
        update = compact_supervisor_context(state_sup)
        assert "supervisor_messages" in update
        assert len(update["supervisor_messages"]) == 2


# ===== 5. SUPERVISOR GATHER FAILURE HANDLING =====

@pytest.mark.asyncio
async def test_supervisor_tools_gather_failure():
    """Verify supervisor_tools exits cleanly with END and empty/existing notes when gather fails."""
    # Scenario: ConductResearch tool called by supervisor
    tool_calls = [
        {"name": "ConductResearch", "args": {"research_topic": "Topic A"}, "id": "call_a"},
    ]
    aimsg = AIMessage(content="", tool_calls=tool_calls)
    state = SupervisorState(
        supervisor_messages=[aimsg],
        research_iterations=1,
        research_brief="brief",
        notes=[],
        raw_notes=[],
    )
    
    # Mock researcher_agent.ainvoke to raise an exception
    with patch("deep_research_from_scratch.multi_agent_supervisor.researcher_agent.ainvoke") as mock_ainvoke:
        mock_ainvoke.side_effect = ValueError("Sub-agent process timeout")
        
        cmd = await supervisor_tools(state)
        assert isinstance(cmd, Command)
        assert cmd.goto == "__end__"
        # Since it exited cleanly due to exception, notes should be returned based on get_notes_from_tool_calls
        # which will be empty since there are no ToolMessages in supervisor_messages history yet.
        assert cmd.update.get("notes") == []


# ===== 6. SMOKE TESTS & THINK_TOOL =====

def test_compress_research_human_message_formatting():
    """Verify compress_research_human_message formats correctly with research_topic."""
    topic = "Non-compete covenants enforceability in New Delhi"
    formatted = compress_research_human_message.format(research_topic=topic)
    assert topic in formatted
    assert "LEGAL ISSUE:" in formatted


def test_think_tool_execution():
    """Verify legal think_tool executes correctly with a single string reflection."""
    res = think_tool.invoke("Analyzing Article 141 hierarchy rules.")
    assert "Legal reflection recorded" in res
    assert "Analyzing Article 141 hierarchy rules." in res
