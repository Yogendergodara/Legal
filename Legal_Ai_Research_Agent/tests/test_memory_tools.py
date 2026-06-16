"""Tests for file-based memory tools, transcript logs, and conversation compaction."""

import json

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)

from deep_research_from_scratch.memory_tools import (
    _safe_compaction_cut,
    build_session_context,
    compact_message_list,
    get_auto_mem_path,
    get_conversation_memory,
    get_session_summary_path,
    get_sessions_dir,
    get_transcript_path,
    read_legal_memories,
    record_message,
    record_verification,
    update_legal_memory,
)


def test_file_memory_crud(configure_test_memory_dir):
    """Verify long-term memory file write + index read operations.

    NOTE: the agent-facing ``save_memory`` / ``search_memory`` tools moved to the
    Legal ai retrieval MCP server (see memory_mcp_tools + the MCP server's
    test_memory.py). These file helpers remain the on-disk implementation shared
    by the MCP server and the graph's load_memory node.
    """
    auto_dir = get_auto_mem_path()

    # update_legal_memory writes a file + registers a pointer in MEMORY.md.
    update_res = update_legal_memory.invoke({
        "file_name": "delhi_facts.md",
        "topic": "Delhi high court limits",
        "content": "Delhi high court enforces covenants during employment.",
    })
    assert "Success" in update_res
    assert (auto_dir / "delhi_facts.md").exists()
    assert "Delhi high court limits" in (auto_dir / "MEMORY.md").read_text(encoding="utf-8")

    # read_legal_memories returns the MEMORY.md index.
    read_res = read_legal_memories.invoke({})
    assert "Delhi high court limits" in read_res


def test_conversation_memory(configure_test_memory_dir):
    """Verify session message recording and retrieval."""
    config = {"configurable": {"thread_id": "test_thread_1"}}

    record_message.invoke({"role": "user", "content": "Query 1"}, config=config)
    record_message.invoke({"role": "assistant", "content": "Response 1"}, config=config)

    convo = get_conversation_memory.invoke({"session_id": "test_thread_1"})
    assert "Query 1" in convo
    assert "Response 1" in convo


def test_record_verification_audit_log(configure_test_memory_dir):
    """Verify report verification audit log is written correctly."""
    session_id = "test_audit_session"
    result = {"passed": False, "fabricated_or_unverified_citations": ["AIR 2024 SC 99"]}
    
    record_verification(session_id, result)
    
    audit_file = get_sessions_dir() / f"{session_id}.verification.jsonl"
    assert audit_file.exists()
    
    lines = audit_file.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["sessionId"] == session_id
    assert data["verification"] == result


def test_safe_compaction_cut():
    """Verify the compaction boundary does not split tool-call / response pairs."""
    # Scenario 1: Basic cut with no tool messages
    messages1 = [HumanMessage(content=f"m {i}", id=str(i)) for i in range(10)]
    assert _safe_compaction_cut(messages1, keep_recent=5) == 5

    # Scenario 2: ToolMessage at the boundary cut index
    # messages[:5] is summarized, messages[5:] is kept.
    # If messages[5] is a ToolMessage, it must be pushed forward.
    messages2 = [
        HumanMessage(content="m 0", id="0"),
        HumanMessage(content="m 1", id="1"),
        HumanMessage(content="m 2", id="2"),
        AIMessage(content="", tool_calls=[{"name": "t", "id": "t1", "args": {}}], id="3"),
        ToolMessage(content="result", name="t", tool_call_id="t1", id="4"),
        HumanMessage(content="m 5", id="5"),
    ]
    # If keep_recent = 2, naive cut index is 4 (len=6 - 2 = 4)
    # But index 4 is a ToolMessage, so it should push forward to 5.
    assert _safe_compaction_cut(messages2, keep_recent=2) == 5


def test_compact_message_list(configure_test_memory_dir):
    """Verify list compaction removes older turns and writes boundary to transcript."""
    session_id = "test_compact_session"
    messages = [HumanMessage(content=f"msg {i}", id=str(i)) for i in range(15)]
    
    # 1. Below threshold (threshold=12) -> returns empty
    res_short = compact_message_list(messages, keep_recent=5, threshold=20, session_id=session_id)
    assert res_short == []

    # 2. Above threshold -> returns removals and summary system message
    res_long = compact_message_list(messages, keep_recent=5, threshold=10, session_id=session_id)
    assert len(res_long) > 0
    
    # Check that it returns RemoveMessages for the compacted messages
    removes = [m for m in res_long if isinstance(m, RemoveMessage)]
    # length is 15. keep_recent is 5. cut is 10.
    assert len(removes) == 10
    assert all(r.id in [str(i) for i in range(10)] for r in removes)
    
    # Check that it returns a summary SystemMessage
    system_msgs = [m for m in res_long if isinstance(m, SystemMessage)]
    assert len(system_msgs) == 1
    assert "summary" in system_msgs[0].content.lower()

    # Check transcript has the compaction boundary marker
    transcript_path = get_transcript_path(session_id)
    assert transcript_path.exists()
    transcript_lines = transcript_path.read_text(encoding="utf-8").strip().split("\n")
    boundary_marker = json.loads(transcript_lines[-1])
    assert boundary_marker["type"] == "system"
    assert boundary_marker["subtype"] == "compact_boundary"
    assert boundary_marker["compactMetadata"]["compactedMessageCount"] == 10


def test_build_session_context_short(configure_test_memory_dir):
    """Verify build_session_context for short session returns verbatim recent turns."""
    session_id = "test_short_context"
    record_message.invoke({"role": "user", "content": "Question 1"}, config={"configurable": {"thread_id": session_id}})
    record_message.invoke({"role": "assistant", "content": "Answer 1"}, config={"configurable": {"thread_id": session_id}})

    context = build_session_context(session_id, keep_recent=5, threshold=10)
    assert "Question 1" in context
    assert "Answer 1" in context
    assert "Running summary" not in context


def test_build_session_context_long(configure_test_memory_dir):
    """Verify build_session_context for long session compiles summary and rolling context."""
    session_id = "test_long_context"
    
    # Write 15 turns to transcript
    for i in range(15):
        role = "user" if i % 2 == 0 else "assistant"
        record_message.invoke(
            {"role": role, "content": f"Turn {i} message details"},
            config={"configurable": {"thread_id": session_id}},
        )

    # Call build_session_context with a low threshold to trigger roll-up compaction
    context = build_session_context(session_id, keep_recent=4, threshold=3)
    
    # Verify the summary file was created
    summary_path = get_session_summary_path(session_id)
    assert summary_path.exists()
    
    summary_data = json.loads(summary_path.read_text(encoding="utf-8"))
    assert "summary" in summary_data
    assert summary_data["summarized_count"] == 11  # 15 - 4 recent = 11 summarized

    # Verify return context contains summary and verbatim recent
    assert "Running summary of earlier conversation" in context
    assert "Most recent turns" in context
    assert "Turn 14 message details" in context  # Verbatim recent
