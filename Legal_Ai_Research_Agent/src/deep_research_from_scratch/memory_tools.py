
"""File-based Memory and Session Tools.

Python port of the TypeScript memory architecture (`sessionStorage.ts` +
`memdir.ts`). Provides two of the three memory layers in plain files:

- Layer 2 (Session memory): append-only JSONL transcript per session, one file
  per ``session_id`` -- the equivalent of ``sessionStorage.ts``.
- Layer 3 (Long-term memory): a ``MEMORY.md`` index plus linked detail files,
  with context-window-protecting truncation -- the equivalent of ``memdir.ts``.

The tools (`save_memory`, `search_memory`, `get_conversation_memory`,
`record_message`) are LangChain tools that can be bound to any agent. The
session id is read from the LangGraph ``RunnableConfig`` (``thread_id``), so it
plays nicely with checkpointed graphs while remaining pure-file underneath.
"""

import json
import os
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from typing_extensions import List

# Serializes all memory/transcript file writes. LangGraph runs sync nodes in a
# thread pool, so parallel research sub-agents can otherwise interleave or
# corrupt the append-only JSONL and the MEMORY.md index.
_write_lock = threading.Lock()

from langchain_core.messages import (
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
    get_buffer_string,
)
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from pydantic import BaseModel, Field

# ===== CONSTANTS (ported from memdir.ts) =====
from deep_research_from_scratch.config import config

ENTRYPOINT_NAME = "MEMORY.md"
MAX_ENTRYPOINT_LINES = config.MAX_ENTRYPOINT_LINES
MAX_ENTRYPOINT_BYTES = config.MAX_ENTRYPOINT_BYTES

# Message types that participate in the persistent transcript (isTranscriptMessage)
TRANSCRIPT_MESSAGE_TYPES = {"user", "assistant", "attachment", "system"}


def get_memory_root() -> Path:
    """Resolve the root directory that holds all memory + session files.

    Configurable via the ``DEEP_RESEARCH_MEMORY_DIR`` environment variable;
    defaults to a ``./memory`` folder in the current working directory.

    Returns:
        Path to the memory root (created if missing).
    """
    root = Path(os.environ.get("DEEP_RESEARCH_MEMORY_DIR", "memory")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_auto_mem_path() -> Path:
    """Directory holding MEMORY.md and linked long-term memory files."""
    path = get_memory_root() / "auto"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_sessions_dir() -> Path:
    """Directory holding per-session JSONL transcripts."""
    path = get_memory_root() / "sessions"
    path.mkdir(parents=True, exist_ok=True)
    return path


# ===== SESSION ID RESOLUTION =====

def get_session_id(config: RunnableConfig | None) -> str:
    """Extract the session id from a LangGraph config (``thread_id``).

    Args:
        config: The RunnableConfig passed to the tool/node. ``thread_id`` under
            ``configurable`` is used as the session id (the equivalent of
            ``getSessionId()`` in the TS code).

    Returns:
        The session id string, or ``"default"`` when none is supplied.
    """
    if config and "configurable" in config:
        return config["configurable"].get("thread_id", "default")
    return "default"


# ===== 1. SESSION STORAGE (ported from sessionStorage.ts) =====

def get_transcript_path(session_id: str) -> Path:
    """Path to the JSONL transcript file for a session."""
    return get_sessions_dir() / f"{session_id}.jsonl"


def is_transcript_message(entry: dict) -> bool:
    """Whether an entry should be persisted in the transcript history."""
    return entry.get("type") in TRANSCRIPT_MESSAGE_TYPES


def record_transcript(session_id: str, role: str, content: str) -> None:
    """Append a single message to the session's JSONL transcript.

    This is the file-based equivalent of ``recordTranscript`` -- an append-only
    log so the full history is never lost, even when the in-context window is
    later compacted.

    Args:
        session_id: The session/thread id.
        role: Message role, e.g. ``"user"`` or ``"assistant"``.
        content: Message text content.
    """
    entry = {
        "type": role if role in TRANSCRIPT_MESSAGE_TYPES else "user",
        "uuid": str(uuid.uuid4()),
        "timestamp": datetime.now().isoformat(),
        "sessionId": session_id,
        "message": {"role": role, "content": content},
    }
    if not is_transcript_message(entry):
        return
    path = get_transcript_path(session_id)
    with _write_lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


def load_transcript(session_id: str) -> List[dict]:
    """Read back a session's full transcript from its JSONL file.

    Args:
        session_id: The session/thread id.

    Returns:
        List of message entries (empty list if the session has no history).
    """
    path = get_transcript_path(session_id)
    if not path.exists():
        return []
    messages: List[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if is_transcript_message(entry):
                messages.append(entry)
    return messages


# ===== 2. LONG-TERM MEMORY INDEX (ported from memdir.ts) =====

@dataclass
class EntrypointTruncation:
    content: str
    line_count: int
    byte_count: int
    was_line_truncated: bool
    was_byte_truncated: bool


def truncate_entrypoint_content(raw: str) -> EntrypointTruncation:
    """Truncate MEMORY.md content to line/byte limits to protect the context window.

    Direct port of ``truncateEntrypointContent``: the index is injected into the
    system prompt every turn, so it must stay small. Details live in linked
    files the agent reads on demand.

    Args:
        raw: Raw MEMORY.md content.

    Returns:
        EntrypointTruncation with the (possibly truncated) content and flags.
    """
    trimmed = raw.strip()
    content_lines = trimmed.split("\n")
    line_count = len(content_lines)
    byte_count = len(trimmed)

    was_line_truncated = line_count > MAX_ENTRYPOINT_LINES
    was_byte_truncated = byte_count > MAX_ENTRYPOINT_BYTES

    if not was_line_truncated and not was_byte_truncated:
        return EntrypointTruncation(
            content=trimmed,
            line_count=line_count,
            byte_count=byte_count,
            was_line_truncated=False,
            was_byte_truncated=False,
        )

    truncated = (
        "\n".join(content_lines[:MAX_ENTRYPOINT_LINES])
        if was_line_truncated
        else trimmed
    )

    if len(truncated) > MAX_ENTRYPOINT_BYTES:
        cut_at = truncated.rfind("\n", 0, MAX_ENTRYPOINT_BYTES)
        truncated = truncated[: cut_at if cut_at > 0 else MAX_ENTRYPOINT_BYTES]

    return EntrypointTruncation(
        content=truncated + f"\n\n> WARNING: {ENTRYPOINT_NAME} truncated. Keep index concise.",
        line_count=line_count,
        byte_count=byte_count,
        was_line_truncated=was_line_truncated,
        was_byte_truncated=was_byte_truncated,
    )


def build_memory_lines(display_name: str, memory_dir: Path) -> List[str]:
    """Build the instructions block telling the LLM how to use file memory.

    Port of ``buildMemoryLines``.
    """
    return [
        f"# {display_name}",
        "",
        f"You have a persistent, file-based memory system at `{memory_dir}`.",
        "",
        "Use this memory system to save information about the user, preferences, "
        "and long-term project contexts.",
        "",
        "## How to save memories",
        "Step 1: Write details to its own file (e.g. `user_preferences.md`) via `save_memory`.",
        f"Step 2: A pointer is added in `{ENTRYPOINT_NAME}` in the format: "
        "`- [Title](file.md) - one-line hook`.",
        "- Keep memory organized semantically.",
        "- Update or delete memories if they become wrong or outdated.",
        "- Do not write duplicate memories.",
        "- Use `search_memory` to recall details before answering.",
    ]


def build_memory_prompt(display_name: str = "auto memory", memory_dir: Path | None = None) -> str:
    """Build the full memory prompt including current MEMORY.md content.

    Port of ``buildMemoryPrompt``.
    """
    memory_dir = memory_dir or get_auto_mem_path()
    entrypoint = memory_dir / ENTRYPOINT_NAME

    entrypoint_content = ""
    if entrypoint.exists():
        entrypoint_content = entrypoint.read_text(encoding="utf-8")

    lines = build_memory_lines(display_name, memory_dir)

    if entrypoint_content.strip():
        t = truncate_entrypoint_content(entrypoint_content)
        lines += [f"## {ENTRYPOINT_NAME}", "", t.content]
    else:
        lines += [
            f"## {ENTRYPOINT_NAME}",
            "",
            f"Your {ENTRYPOINT_NAME} is empty. Save new memories here.",
        ]

    return "\n".join(lines)


def load_memory_prompt() -> str:
    """Load the unified memory prompt for insertion into the system prompt.

    Port of ``loadMemoryPrompt`` -- call this inside a node and prepend the
    result to the system prompt so the agent always sees its memory index.
    """
    return build_memory_prompt(display_name="auto memory", memory_dir=get_auto_mem_path())


def _slugify(title: str) -> str:
    """Turn a memory title into a safe filename slug."""
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    return slug or "memory"


def _append_index_pointer(memory_dir: Path, title: str, filename: str, hook: str) -> None:
    """Add (or replace) a pointer line in MEMORY.md for a saved memory file."""
    entrypoint = memory_dir / ENTRYPOINT_NAME
    pointer = f"- [{title}]({filename}) - {hook}"

    with _write_lock:
        existing = entrypoint.read_text(encoding="utf-8") if entrypoint.exists() else ""
        lines = [ln for ln in existing.split("\n") if ln.strip()]
        # Replace an existing pointer to the same file, else append.
        lines = [ln for ln in lines if f"({filename})" not in ln]
        if not lines:
            lines = ["# MEMORY", ""]
        lines.append(pointer)
        entrypoint.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ===== 3. MEMORY TOOLS =====
# NOTE: The agent-facing long-term memory tools ``save_memory`` / ``search_memory``
# now live in ``memory_mcp_tools`` and route through the Legal ai retrieval MCP
# server (``/tools/memory/*``). The file helpers below remain the on-disk
# implementation: they back the MCP server's memory store and the graph's
# ``load_memory`` node, all sharing one ``DEEP_RESEARCH_MEMORY_DIR``.

# ===== 3b. LEGAL LONG-TERM MEMORY TOOLS =====
# These keep the original tool names so existing legal agents/prompts that call
# ``read_legal_memories`` / ``update_legal_memory`` keep working. They share the
# same MEMORY.md index + memory directory.

class UpdateMemorySchema(BaseModel):
    file_name: str = Field(
        description="The target file name, e.g., 'client_acme.md' or 'tax_limits_2024.md'"
    )
    topic: str = Field(
        description="A short 1-line title for the index, e.g., 'Acme Corp Corporate Info'"
    )
    content: str = Field(
        description="The complete markdown contents of the memory file containing facts and notes."
    )


@tool("read_legal_memories")
def read_legal_memories() -> str:
    """Read the central MEMORY.md index of long-term legal facts and client profiles."""
    entrypoint = get_auto_mem_path() / ENTRYPOINT_NAME
    if not entrypoint.exists():
        return f"Your {ENTRYPOINT_NAME} is empty. Save new memories with update_legal_memory."
    content = entrypoint.read_text(encoding="utf-8")
    return truncate_entrypoint_content(content).content


@tool("update_legal_memory", args_schema=UpdateMemorySchema)
def update_legal_memory(file_name: str, topic: str, content: str) -> str:
    """Create or update a long-term legal memory file and register it in MEMORY.md."""
    safe_name = re.sub(r"[^a-zA-Z0-9_\-\.]", "", file_name)
    if not safe_name.endswith(".md"):
        safe_name += ".md"

    memory_dir = get_auto_mem_path()
    target_file = memory_dir / safe_name
    try:
        target_file.write_text(content, encoding="utf-8")
        _append_index_pointer(memory_dir, topic, safe_name, "Persistent legal facts.")
        return f"Success: Memory written to {safe_name} and pointer ensured in {ENTRYPOINT_NAME}."
    except Exception as e:
        return f"Error updating memory: {str(e)}"


@tool(parse_docstring=True)
def get_conversation_memory(session_id: str, limit: int = 20, config: RunnableConfig = None) -> str:
    """Retrieve past messages from the current (or a given) conversation session.

    Reads the append-only JSONL transcript for the session. Pass an empty
    ``session_id`` to use the active session from the runtime config.

    Args:
        session_id: The session id to load. Empty string uses the active session.
        limit: Maximum number of most-recent messages to return.

    Returns:
        Formatted recent conversation history, or a note that none exists.
    """
    sid = session_id or get_session_id(config)
    messages = load_transcript(sid)
    if not messages:
        return f"No conversation history for session '{sid}'."

    recent = messages[-limit:]
    lines = [f"Conversation history for session '{sid}' (last {len(recent)} messages):", ""]
    for m in recent:
        msg = m.get("message", {})
        role = msg.get("role", m.get("type", "unknown"))
        text = msg.get("content", "")
        if isinstance(text, str):
            lines.append(f"[{role}] {text}")
    return "\n".join(lines)


@tool(parse_docstring=True)
def record_message(role: str, content: str, config: RunnableConfig = None) -> str:
    """Persist a single message to the current session's transcript log.

    Append-only write to the session JSONL file (file-based equivalent of
    ``recordTranscript``). The session id comes from the runtime config.

    Args:
        role: The message role, e.g. ``"user"`` or ``"assistant"``.
        content: The message text to persist.

    Returns:
        Confirmation that the message was recorded.
    """
    sid = get_session_id(config)
    record_transcript(sid, role, content)
    return f"Message recorded to session '{sid}'."


# ===== 4. CONTEXT COMPACTION (long-chat memory conservation) =====
# Python equivalent of the `snipReplay` + `recordContextCollapseCommit` logic in
# QueryEngine.ts / sessionStorage.ts. When a conversation grows too long, we
# summarize the oldest messages into a single running summary and drop them from
# the active context window -- while the FULL history stays safe in the JSONL
# transcript on disk. This is how a long chat "keeps its memory" without ever
# blowing the model's token limit.

# How many messages must accumulate before we compact.
COMPACT_THRESHOLD = config.COMPACT_THRESHOLD
# How many of the most-recent messages to keep verbatim after compaction.
KEEP_RECENT_MESSAGES = config.KEEP_RECENT_MESSAGES

COMPACT_PROMPT = (
    "You are compacting a long conversation to conserve the context window. "
    "Summarize the following messages into a concise but complete set of durable "
    "notes that preserve: the user's goals, key facts and entities, decisions "
    "made, and any open/unfinished tasks. Write in third person."
)


def should_compact(messages: list) -> bool:
    """Whether the conversation is long enough to warrant compaction."""
    return len(messages) > COMPACT_THRESHOLD


def record_compact_boundary(session_id: str, compacted_message_count: int) -> None:
    """Write a compaction checkpoint marker to the session transcript.

    File-based equivalent of ``recordContextCollapseCommit`` -- records that a
    compaction happened so the on-disk history reflects the boundary, even
    though the in-context messages were collapsed.
    """
    entry = {
        "type": "system",
        "subtype": "compact_boundary",
        "uuid": str(uuid.uuid4()),
        "timestamp": datetime.now().isoformat(),
        "sessionId": session_id,
        "compactMetadata": {"compactedMessageCount": compacted_message_count},
    }
    path = get_transcript_path(session_id)
    with _write_lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


def _safe_compaction_cut(messages: list, keep_recent: int) -> int:
    """Find a cut index that keeps ~``keep_recent`` messages WITHOUT splitting a
    tool-call/tool-result pair.

    Removing an ``AIMessage`` that carries ``tool_calls`` while keeping its
    matching ``ToolMessage`` (or vice-versa) produces an invalid request that
    most LLM providers reject ("tool_call_ids did not have response messages").
    We therefore push the boundary forward past any leading ``ToolMessage`` in
    the kept window, so each tool group stays wholly on one side of the cut.

    Returns:
        The index ``cut`` such that ``messages[:cut]`` is summarized/dropped and
        ``messages[cut:]`` is kept verbatim.
    """
    cut = max(0, len(messages) - keep_recent)
    # The kept window must not begin with an orphaned tool result.
    while cut < len(messages) and isinstance(messages[cut], ToolMessage):
        cut += 1
    return cut


def compact_message_list(
    messages: list,
    *,
    keep_recent: int = KEEP_RECENT_MESSAGES,
    threshold: int = COMPACT_THRESHOLD,
    session_id: str | None = None,
    summary_label: str = "Conversation summary so far",
) -> list:
    """Tool-call-aware compaction for any agent message list.

    Summarizes the oldest messages into one rolling ``SystemMessage`` and returns
    ``[RemoveMessage(...), ..., SystemMessage(summary)]`` to splice into state via
    an ``add_messages`` reducer. Returns ``[]`` when compaction is not needed.

    Unlike a naive tail-trim, this respects tool-call/result pairing (see
    :func:`_safe_compaction_cut`) so it is safe to run inside agent loops whose
    histories contain ``AIMessage`` tool calls and ``ToolMessage`` results. The
    text to summarize is flattened with ``get_buffer_string`` so the summarizer
    request itself never carries dangling tool calls.
    """
    if len(messages) <= threshold:
        return []

    cut = _safe_compaction_cut(messages, keep_recent)
    to_summarize = messages[:cut]
    if not to_summarize:
        return []

    # Lazy import so the module loads without API keys configured.
    from deep_research_from_scratch.model_config import get_chat_model

    model = get_chat_model("summarizer")
    convo_text = get_buffer_string(to_summarize)
    summary_resp = model.invoke([
        SystemMessage(content=COMPACT_PROMPT),
        HumanMessage(content=convo_text),
    ])
    summary_text = getattr(summary_resp, "content", str(summary_resp))

    if session_id:
        record_compact_boundary(session_id, len(to_summarize))

    removals = [
        RemoveMessage(id=m.id)
        for m in to_summarize
        if getattr(m, "id", None) is not None
    ]
    summary_msg = SystemMessage(content=f"[{summary_label}]\n{summary_text}")
    return [*removals, summary_msg]


def compact_conversation(state: dict, config: RunnableConfig = None) -> dict:
    """LangGraph node: summarize + drop old messages when the chat gets long.

    Use as a node before your LLM-call node. Returns a state update that removes
    the oldest messages via ``RemoveMessage`` and inserts a single rolling-summary
    ``SystemMessage`` in their place, or ``{}`` when no compaction is needed.

    Args:
        state: Graph state containing a ``messages`` list (LangGraph messages).
        config: RunnableConfig; ``thread_id`` is used as the session id.

    Returns:
        A ``{"messages": [...]}`` state update, or ``{}`` if no compaction needed.
    """
    update = compact_message_list(
        state.get("messages", []),
        session_id=get_session_id(config),
    )
    return {"messages": update} if update else {}


# ===== 5. ROLLING SESSION SUMMARY (continuous conversation, no context loss) =====
# A long, multi-turn conversation cannot fit verbatim in the context window. To
# keep continuity WITHOUT losing context we maintain, per session:
#   - a persisted rolling summary of everything older than the recent window, and
#   - the last few turns verbatim.
# The full transcript always remains on disk (source of truth), and only the
# *new* backlog is summarized occasionally, so this stays fast (few LLM calls)
# and memory-bounded (injection size is capped regardless of session length).

# Once the un-summarized backlog exceeds this many messages, fold the older part
# into the rolling summary.
SESSION_SUMMARY_THRESHOLD = config.SESSION_SUMMARY_THRESHOLD
# How many of the most recent messages to always keep verbatim.
SESSION_KEEP_RECENT = config.SESSION_KEEP_RECENT

SESSION_SUMMARY_PROMPT = (
    "You maintain a running summary of a legal research conversation so it can "
    "continue across many turns without losing context. Update the EXISTING "
    "summary by folding in the NEW messages. Preserve precisely: the user's "
    "goals/questions, jurisdiction, key facts and entities, any statutes/cases/"
    "citations mentioned, decisions made, and open/unfinished tasks. Keep it "
    "concise and factual (third person). Do NOT invent anything."
)


def record_verification(session_id: str, result: dict) -> None:
    """Append a report-verification result to the session's audit log.

    Creates an append-only ``{session_id}.verification.jsonl`` so there is a
    durable record of what the verification gate checked and flagged for each
    delivered memo (important for a legal audit trail).
    """
    entry = {
        "uuid": str(uuid.uuid4()),
        "timestamp": datetime.now().isoformat(),
        "sessionId": session_id,
        "verification": result,
    }
    path = get_sessions_dir() / f"{session_id}.verification.jsonl"
    with _write_lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def get_session_summary_path(session_id: str) -> Path:
    """Path to the persisted rolling-summary file for a session."""
    return get_sessions_dir() / f"{session_id}.summary.json"


def load_session_summary(session_id: str) -> tuple:
    """Load ``(summary_text, summarized_count)`` for a session.

    ``summarized_count`` is how many transcript entries are already folded into
    the summary, so only newer messages need summarizing next time.
    """
    path = get_session_summary_path(session_id)
    if not path.exists():
        return "", 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("summary", ""), int(data.get("summarized_count", 0))
    except (json.JSONDecodeError, ValueError, OSError):
        return "", 0


def save_session_summary(session_id: str, summary: str, summarized_count: int) -> None:
    """Persist the rolling summary + how many messages it covers."""
    path = get_session_summary_path(session_id)
    payload = {
        "summary": summary,
        "summarized_count": summarized_count,
        "updated_at": datetime.now().isoformat(),
    }
    with _write_lock:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _entries_to_text(entries: List[dict]) -> str:
    """Render transcript entries as ``[role] content`` lines."""
    lines = []
    for e in entries:
        msg = e.get("message", {})
        role = msg.get("role", e.get("type", "unknown"))
        text = msg.get("content", "")
        if isinstance(text, str) and text.strip():
            lines.append(f"[{role}] {text}")
    return "\n".join(lines)


def build_session_context(
    session_id: str,
    keep_recent: int = SESSION_KEEP_RECENT,
    threshold: int = SESSION_SUMMARY_THRESHOLD,
) -> str:
    """Build bounded conversation context for a (possibly very long) session.

    Returns a string with a rolling summary of older turns plus the most recent
    turns verbatim. Updates + persists the summary only when the un-summarized
    backlog grows past ``threshold`` (so it is cheap on most turns). The full
    transcript stays on disk untouched, so nothing is ever lost.
    """
    transcript = load_transcript(session_id)
    if not transcript:
        return "No prior conversation on record for this session."

    summary, summarized_count = load_session_summary(session_id)
    # Guard against a summary that points past a transcript (e.g. reset).
    if summarized_count > len(transcript):
        summarized_count = 0
        summary = ""

    backlog = transcript[summarized_count:]

    # Fold the older part of the backlog into the rolling summary when it is big
    # enough that we should not inject it all verbatim.
    if len(backlog) > threshold + keep_recent:
        to_fold = transcript[summarized_count: len(transcript) - keep_recent]
        if to_fold:
            from deep_research_from_scratch.model_config import get_chat_model

            model = get_chat_model("summarizer")
            prompt = (
                f"{SESSION_SUMMARY_PROMPT}\n\n"
                f"### Existing summary\n{summary or '(none yet)'}\n\n"
                f"### New messages to fold in\n{_entries_to_text(to_fold)}"
            )
            resp = model.invoke([SystemMessage(content=SESSION_SUMMARY_PROMPT), HumanMessage(content=prompt)])
            summary = getattr(resp, "content", str(resp))
            summarized_count = len(transcript) - keep_recent
            save_session_summary(session_id, summary, summarized_count)

    recent = transcript[-keep_recent:] if keep_recent > 0 else []
    parts = []
    if summary.strip():
        parts.append(f"#### Running summary of earlier conversation\n{summary.strip()}")
    if recent:
        parts.append(f"#### Most recent turns (verbatim)\n{_entries_to_text(recent)}")
    return "\n\n".join(parts) if parts else "No prior conversation on record for this session."
