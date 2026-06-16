"""Normal Research Pipeline — fast legal answers with 2-3 retrieval rounds.

This is the lightweight sibling of the full deep-research pipeline.
Goal: produce a concise, verified answer (500-1200 words) in a fraction of
the time needed for a full legal memorandum.

Pipeline:
    load_memory
        ↓
    compact_conversation
        ↓
    write_research_brief  (same brief, but tells the researcher to be concise)
        ↓
    normal_researcher     (simple search→fetch loop, 2-3 rounds max)
        ↓
    generate_normal_answer
        ↓
    END
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from deep_research_from_scratch.config import config as app_config
from deep_research_from_scratch.model_config import (
    ainvoke_with_retry,
    get_chat_model,
)
from deep_research_from_scratch.research_agent_scope import (
    compact_conversation,
    load_memory,
    write_research_brief,
)
from deep_research_from_scratch.mcp_client import get_retrieval_client
from deep_research_from_scratch.retrieval_bridge import (
    format_fetch_result,
    format_retrieval_results,
)
from deep_research_from_scratch.source_registry import (
    RetrievedSource,
    filter_citable_sources,
    format_writer_source_registry,
    source_from_fetch,
    sources_from_search_hits,
)
from deep_research_from_scratch.state_scope import AgentInputState, AgentState
from deep_research_from_scratch.utils import get_today_str

# ── LLM instances ──────────────────────────────────────────────────────────────

_answer_model = get_chat_model("writer", max_tokens=4096)
_search_model = get_chat_model("reasoning", temperature=0.0)

# ── Prompts ────────────────────────────────────────────────────────────────────

_PLANNER_PROMPT = """\
You are an Indian legal research assistant. Given the research brief below,
generate up to {max_queries} focused search queries to find the most relevant
statutes, sections, and judgments. Return ONLY the queries, one per line,
no numbering, no extra text.

Research brief:
{brief}

Date: {date}
"""

_ANSWER_PROMPT = """\
You are an Indian legal research assistant writing a concise legal answer.

Research brief:
{brief}

Retrieved sources and findings:
{findings}

Source registry:
{source_registry}

Date: {date}

Instructions:
- Write a clear, direct answer of 500-1200 words.
- Structure: brief answer → key statutes/sections → key cases → practical takeaway.
- Cite sources inline as [1], [2], etc., matching the source registry.
- Only cite sources that appear in the source registry — never invent citations.
- Do NOT write a full legal memorandum. Keep it conversational and focused.
- End with a short "Suggested Follow-up Queries" section with 2-3 numbered questions.
- Add a one-line disclaimer: "This is AI-assisted legal research; consult a lawyer for advice."
"""

# ── Helper: collect sources from state ────────────────────────────────────────


def _collect_sources(state: AgentState) -> list[RetrievedSource]:
    return [
        item if isinstance(item, RetrievedSource) else RetrievedSource(**item)
        for item in (state.get("retrieved_sources") or [])
    ]


# ── Node: normal_researcher — 2-3 round search + fetch loop ───────────────────


async def normal_researcher(state: AgentState, config: RunnableConfig) -> dict:
    """Lightweight retrieval loop — 2-3 search + fetch rounds using async MCP client."""
    brief = state.get("research_brief") or ""
    if not brief:
        for msg in reversed(state.get("messages", [])):
            if getattr(msg, "type", None) == "human":
                brief = str(getattr(msg, "content", ""))
                break

    max_queries = app_config.NORMAL_MAX_SEARCH_QUERIES
    max_fetches = app_config.NORMAL_MAX_FETCHES
    results_per_query = app_config.NORMAL_RESULTS_PER_QUERY
    tenant_id = (config.get("configurable") or {}).get("tenant_id")

    # 1. Plan search queries via LLM
    planner_prompt = _PLANNER_PROMPT.format(
        max_queries=max_queries,
        brief=brief,
        date=get_today_str(),
    )
    planner_response = await ainvoke_with_retry(
        _search_model,
        [HumanMessage(content=planner_prompt)],
    )
    raw_queries = str(getattr(planner_response, "content", "") or "").strip()
    queries = [q.strip() for q in raw_queries.splitlines() if q.strip()][:max_queries]
    if not queries:
        queries = [brief[:300]]

    # 2. Execute searches and fetch top results asynchronously
    client = get_retrieval_client()
    all_snippets: list[str] = []
    sources: list[RetrievedSource] = list(_collect_sources(state))
    fetched_urls: set[str] = {s.url for s in sources if s.url}
    fetch_count = 0

    for query in queries:
        try:
            hits = await client.search(
                query=query,
                search_type="all",
                max_results=results_per_query,
                tenant_id=tenant_id,
            )
        except Exception:  # noqa: BLE001
            hits = []

        snippet_text = format_retrieval_results(hits)
        if snippet_text:
            all_snippets.append(f"[Search: {query}]\n{snippet_text}")

        new_sources = sources_from_search_hits(hits)
        sources.extend(new_sources)

        # Fetch the top 2 URLs for each query (up to max_fetches total)
        for src in new_sources[:2]:
            if fetch_count >= max_fetches:
                break
            url = src.url or ""
            if not url or url in fetched_urls:
                continue
            fetched_urls.add(url)
            fetch_count += 1
            try:
                data = await client.fetch(url=url)
                full_text = format_fetch_result(data, url)
                if full_text:
                    all_snippets.append(f"[Fetched: {url}]\n{full_text[:3000]}")
                fetched_src = source_from_fetch(url, data, app_config.FETCH_MAX_CHARS)
                if fetched_src is not None:
                    fetched_src.fetched = True
                    sources.append(fetched_src)
            except Exception:  # noqa: BLE001
                pass

    findings = "\n\n".join(all_snippets) if all_snippets else "No sources retrieved."

    return {
        "notes": [findings],
        "raw_notes": [findings],
        "retrieved_sources": sources,
    }


# ── Node: generate_normal_answer ───────────────────────────────────────────────


async def generate_normal_answer(state: AgentState, config: RunnableConfig) -> dict:
    """Draft a concise answer from normal research findings."""
    brief = state.get("research_brief") or ""
    notes = state.get("notes") or []
    findings = "\n\n".join(notes) if notes else "No findings."

    # Trim findings to a reasonable context budget
    char_budget = app_config.NORMAL_FINDINGS_CHAR_BUDGET
    if len(findings) > char_budget:
        findings = findings[:char_budget] + "\n\n[Findings truncated for brevity.]"

    sources = filter_citable_sources(_collect_sources(state))
    source_registry = format_writer_source_registry(sources)

    prompt = _ANSWER_PROMPT.format(
        brief=brief,
        findings=findings,
        source_registry=source_registry or "No citable sources available.",
        date=get_today_str(),
    )

    try:
        result = await ainvoke_with_retry(
            _answer_model,
            [HumanMessage(content=prompt)],
        )
        content = str(getattr(result, "content", "") or "").strip()
    except Exception as exc:  # noqa: BLE001
        content = (
            "# Legal Research Answer\n\n"
            f"Could not generate an answer due to an error: {exc}.\n\n"
            "Please retry your question."
        )

    return {
        "final_report": content,
        "messages": [AIMessage(content=content)],
    }


# ── Graph construction ──────────────────────────────────────────────────────────

normal_researcher_builder = StateGraph(AgentState, input_schema=AgentInputState)

normal_researcher_builder.add_node("load_memory", load_memory)
normal_researcher_builder.add_node("compact_conversation", compact_conversation)
normal_researcher_builder.add_node("write_research_brief", write_research_brief)
normal_researcher_builder.add_node("normal_researcher", normal_researcher)
normal_researcher_builder.add_node("generate_normal_answer", generate_normal_answer)

normal_researcher_builder.add_edge(START, "load_memory")
normal_researcher_builder.add_edge("load_memory", "compact_conversation")
normal_researcher_builder.add_edge("compact_conversation", "write_research_brief")
normal_researcher_builder.add_edge("write_research_brief", "normal_researcher")
normal_researcher_builder.add_edge("normal_researcher", "generate_normal_answer")
normal_researcher_builder.add_edge("generate_normal_answer", END)
