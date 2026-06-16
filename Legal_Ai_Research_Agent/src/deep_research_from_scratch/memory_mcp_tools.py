"""Long-term memory tools backed by the Legal ai retrieval MCP server.

These keep the original tool names (``save_memory`` / ``search_memory``) and
signatures so existing prompts and the research agent bind them unchanged, but
all file I/O now happens on the MCP server (see ``/tools/memory/*``). The agent
no longer touches the memory files directly.
"""

from langchain_core.tools import tool

from deep_research_from_scratch.retrieval_bridge import (
    make_mcp_memory_save_provider,
    make_mcp_memory_search_provider,
)

_save_provider = make_mcp_memory_save_provider()
_search_provider = make_mcp_memory_search_provider()


@tool(parse_docstring=True)
def save_memory(title: str, content: str, hook: str = "") -> str:
    """Save a durable, verified legal fact to long-term memory for future reuse.

    Persists the fact on the Legal ai retrieval MCP server, which writes it to a
    markdown file and indexes a one-line pointer in MEMORY.md.

    Args:
        title: Short human-readable title for this memory (used in the index).
        content: The full detail to remember. Stored in its own file.
        hook: A one-line summary shown in the MEMORY.md index for quick scanning.

    Returns:
        Confirmation message with the saved file name.
    """
    return _save_provider(title, content, hook)


@tool(parse_docstring=True)
def search_memory(query: str) -> str:
    """Search long-term memory for relevant saved legal facts before searching the web.

    Queries the Legal ai retrieval MCP server, which scans the saved memory files
    and returns the matching contents.

    Args:
        query: Keywords describing what you want to recall.

    Returns:
        Matching memory contents, or a note that nothing was found.
    """
    return _search_provider(query)
