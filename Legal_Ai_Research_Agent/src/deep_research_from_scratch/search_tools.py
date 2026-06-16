
"""Legal search and fetch tools backed by the Legal ai Retrieval MCP server.

The agents and prompts refer to these tools:
- ``web_search``: Fan-out keyword search returning snippets with URLs.
- ``semantic_search``: Vector search over indexed statutes/judgments.
- ``fetch_url``: Full-page fetch for a specific URL (use after search to
  read the complete judgment text and extract precise citations).
"""

from langchain_core.tools import InjectedToolArg, tool
from typing_extensions import Annotated, Literal

from deep_research_from_scratch.retrieval_bridge import run_fetch, run_search, run_semantic_search


def _run_search(query: str, max_results: int, topic: str) -> str:
    """Route a search to the Legal ai retrieval MCP server."""
    text, _ = run_search(query, max_results)
    return text


@tool(parse_docstring=True)
def web_search(
    query: str,
    max_results: Annotated[int, InjectedToolArg] = 5,
    topic: Annotated[Literal["general", "news", "finance"], InjectedToolArg] = "general",
) -> str:
    """Search for Indian legal sources via the Legal ai retrieval MCP server.

    Use this to find bare acts, sections, and judgments from authoritative Indian
    legal sources (India Code, e-SCR, Supreme Court / High Court sites). Returns
    formatted results with titles, URLs, and content snippets.

    For precise citations, follow up with fetch_url on any promising URL from
    these results — especially indiankanoon.org, indiacode.nic.in, or court sites.

    Args:
        query: A single, specific legal search query. Prefer queries targeting
            indiankanoon.org, indiacode.nic.in, or official court domains for primary sources.
        max_results: Maximum number of results to return.
        topic: Topic hint for the search ('general', 'news', 'finance').

    Returns:
        Formatted string of search results to ground the legal analysis.
    """
    return _run_search(query, max_results, topic)


@tool(parse_docstring=True)
def semantic_search(
    query: str,
    max_results: Annotated[int, InjectedToolArg] = 5,
) -> str:
    """Vector search over indexed Indian legal documents (statutes, judgments).

    Use for conceptual or statute-discovery queries where keyword search may miss
    relevant passages. Always follow up with fetch_url on returned URLs before
    citing. Falls back gracefully if semantic index is unavailable.

    Args:
        query: Natural-language legal research query.
        max_results: Maximum number of results to return.

    Returns:
        Formatted semantic search results with URLs and snippets.
    """
    text, _ = run_semantic_search(query, max_results)
    return text


@tool(parse_docstring=True)
def fetch_url(url: str) -> str:
    """Fetch the FULL TEXT of a legal document or webpage from a URL.

    Use this after web_search whenever a result URL points to an authoritative
    source — especially:
    - indiankanoon.org (case judgments with neutral citations)
    - indiacode.nic.in (official bare act / statute text)
    - digiscr.sci.gov.in (e-SCR Supreme Court neutral citations)
    - main.sci.gov.in (Supreme Court of India orders)
    - any High Court official website

    The snippet returned by web_search is too short to capture full citations.
    Fetching the URL returns the complete judgment text including the exact
    neutral citation, bench composition, ratio decidendi, and holding — which
    can then be cited accurately in the memorandum.

    Args:
        url: The full http/https URL to fetch. Must be from a search result.

    Returns:
        Full cleaned page text for citation extraction and legal analysis.
    """
    text, _ = run_fetch(url)
    return text
