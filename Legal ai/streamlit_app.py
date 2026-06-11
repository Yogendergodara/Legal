"""Streamlit UI for testing the Retrieval MCP server and Legal AI Platform."""

from __future__ import annotations

import json
from typing import Any

import httpx
import streamlit as st

DEFAULT_RETRIEVAL_URL = "http://localhost:8001"
DEFAULT_PLATFORM_URL = "http://localhost:8080"
TIMEOUT_SECONDS = 300

st.set_page_config(
    page_title="Legal AI Tester",
    page_icon="⚖️",
    layout="wide",
)

st.title("Legal AI Tester")
st.caption(
    "Test the **Research Agent** (platform gateway) or individual **Retrieval MCP** tools."
)


def _api(
    base_url: str,
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    timeout: int = TIMEOUT_SECONDS,
) -> tuple[int | None, Any, str | None]:
    url = f"{base_url.rstrip('/')}{path}"
    try:
        with httpx.Client(timeout=timeout) as client:
            if method == "GET":
                response = client.get(url)
            else:
                response = client.post(url, json=json_body)
        try:
            payload: Any = response.json()
        except json.JSONDecodeError:
            payload = response.text
        return response.status_code, payload, None
    except httpx.ConnectError:
        return None, None, f"Cannot connect to {url}. Is the server running?"
    except httpx.TimeoutException:
        return None, None, f"Request timed out after {timeout}s."
    except httpx.HTTPError as exc:
        return None, None, str(exc)


def _show_response(status: int | None, payload: Any, error: str | None) -> None:
    if error:
        st.error(error)
        return
    if status is None:
        st.error("No response received.")
        return
    if status >= 400:
        st.error(f"HTTP {status}")
    else:
        st.success(f"HTTP {status}")
    if payload is not None:
        with st.expander("Raw JSON response", expanded=False):
            if isinstance(payload, (dict, list)):
                st.json(payload)
            else:
                st.code(str(payload))


def _render_search_results(results: list[dict[str, Any]]) -> None:
    if not results:
        st.info("No results returned.")
        return
    for i, hit in enumerate(results, start=1):
        score = hit.get("relevance_score") or hit.get("similarity_score")
        score_label = f" · score {score:.2f}" if isinstance(score, (int, float)) else ""
        label = (
            f"{i}. [{hit.get('source_type', '?')}] "
            f"{hit.get('title', 'Untitled')}{score_label}"
        )
        with st.expander(label, expanded=i == 1):
            st.markdown(hit.get("text_snippet", ""))
            if hit.get("url"):
                st.link_button("Open source", hit["url"], key=f"open_{i}")
            cols = st.columns(3)
            cols[0].write(f"**source_id:** `{hit.get('source_id', '')}`")
            cols[1].write(f"**jurisdiction:** {hit.get('jurisdiction', '—')}")
            cols[2].write(f"**type:** {hit.get('source_type', '—')}")
            if hit.get("metadata"):
                st.json(hit["metadata"])


with st.sidebar:
    st.header("Connections")
    retrieval_url = st.text_input(
        "Retrieval MCP URL",
        value=DEFAULT_RETRIEVAL_URL,
        key="retrieval_url",
    )
    platform_url = st.text_input(
        "Platform gateway URL",
        value=DEFAULT_PLATFORM_URL,
        key="platform_url",
    )

    if st.button("Check retrieval health", use_container_width=True, key="health_retrieval"):
        status, payload, error = _api(retrieval_url, "GET", "/health", timeout=10)
        if error:
            st.error(error)
        elif status == 200 and isinstance(payload, dict):
            st.success(
                f"{payload.get('service')} v{payload.get('version')} — {payload.get('status')}"
            )
        else:
            st.warning(f"Unexpected response (HTTP {status})")

    if st.button("Check platform health", use_container_width=True, key="health_platform"):
        status, payload, error = _api(platform_url, "GET", "/health", timeout=10)
        if error:
            st.error(error)
        elif status == 200 and isinstance(payload, dict):
            st.success(
                f"{payload.get('service')} v{payload.get('version')} — {payload.get('status')}"
            )
        else:
            st.warning(f"Unexpected response (HTTP {status})")

    st.divider()
    st.markdown(
        "**Retrieval MCP** (port 8001):\n"
        "```\n"
        "uvicorn mcp.retrieval_server.main:app --port 8001\n"
        "```\n"
        "**Platform** (port 8080):\n"
        "```\n"
        "uvicorn legal_ai_platform.gateway.app:app --port 8080\n"
        "```"
    )

(
    tab_research,
    tab_search,
    tab_fetch,
    tab_semantic,
    tab_citation,
    tab_ingest,
) = st.tabs(
    [
        "Research Agent",
        "Search",
        "Fetch & Extract",
        "Semantic Search",
        "Citation Graph",
        "Ingest Internal",
    ]
)

with tab_research:
    st.subheader("POST /query — Research Agent")
    st.markdown(
        "Full legal research via the platform orchestrator. "
        "Requires the **platform gateway** on port 8080 and the retrieval server on 8001."
    )

    thread_id = st.session_state.get("research_thread_id")
    if thread_id:
        st.info(f"Active session — `thread_id`: `{thread_id}`")
        if st.button("Start new session", key="research_new_session"):
            st.session_state.pop("research_thread_id", None)
            st.session_state.pop("research_awaiting_input", None)
            st.rerun()

    research_query = st.text_area(
        "Legal question",
        value="What is the limitation period for breach of contract in India?",
        height=100,
        key="research_query",
    )
    rc1, rc2 = st.columns(2)
    research_task_type = rc1.selectbox(
        "task_type (optional)",
        ["auto", "research"],
        key="research_task_type",
        help="Leave as auto to let the classifier decide.",
    )
    research_max_results = rc2.number_input(
        "max_results",
        min_value=1,
        max_value=100,
        value=10,
        key="research_max_results",
    )
    research_tenant = st.text_input(
        "tenant_id (optional)",
        value="",
        key="research_tenant",
    )

    awaiting = st.session_state.get("research_awaiting_input", False)
    btn_label = "Send follow-up" if awaiting else "Run research"
    if st.button(btn_label, type="primary", key="research_btn"):
        body: dict[str, Any] = {
            "query": research_query,
            "max_results": int(research_max_results),
        }
        if research_task_type != "auto":
            body["task_type"] = research_task_type
        if research_tenant.strip():
            body["tenant_id"] = research_tenant.strip()
        if thread_id:
            body["thread_id"] = thread_id

        with st.spinner("Research agent working… (may take a few minutes)"):
            status, payload, error = _api(
                platform_url,
                "POST",
                "/query",
                json_body=body,
                timeout=TIMEOUT_SECONDS,
            )

        _show_response(status, payload, error)
        if status == 200 and isinstance(payload, dict):
            if payload.get("thread_id"):
                st.session_state["research_thread_id"] = payload["thread_id"]
            st.session_state["research_awaiting_input"] = payload.get(
                "awaiting_input", False
            )

            if payload.get("awaiting_input"):
                st.warning("The agent needs more information — reply above using the same session.")
                question = payload.get("output", "")
                if question.strip():
                    st.markdown("### Clarification needed")
                    st.markdown(question)
            elif payload.get("success"):
                output = payload.get("output", "")
                if output.strip():
                    st.markdown("### Answer")
                    st.markdown(output)
                else:
                    st.warning(
                        "Request completed but returned no text. "
                        "Check gateway logs or expand Raw JSON response below."
                    )
            else:
                st.error(payload.get("error") or "Research failed.")

            meta = st.columns(3)
            meta[0].write(f"**agent:** {payload.get('agent', '—')}")
            meta[1].write(f"**task_type:** {payload.get('task_type', '—')}")
            meta[2].write(f"**success:** {payload.get('success', False)}")

            if payload.get("artifacts"):
                with st.expander("Artifacts", expanded=False):
                    st.json(payload["artifacts"])
            if payload.get("events"):
                with st.expander("Events", expanded=False):
                    st.json(payload["events"])

with tab_search:
    st.subheader("POST /tools/search")
    query = st.text_area(
        "Query",
        value="limitation period breach of contract India",
        height=80,
        key="search_query",
    )
    c1, c2, c3 = st.columns(3)
    search_type = c1.selectbox(
        "search_type",
        ["all", "web", "internal"],
        key="search_type",
    )
    jurisdiction = c2.text_input("jurisdiction", value="India", key="search_jurisdiction")
    max_results = c3.number_input(
        "max_results", min_value=1, max_value=100, value=10, key="search_max_results"
    )
    tenant_id = st.text_input(
        "tenant_id (required for internal search)", value="", key="search_tenant_id"
    )
    filters_raw = st.text_area(
        "filters (JSON, optional)", value="", height=60, key="search_filters"
    )

    if st.button("Run search", type="primary", key="search_btn"):
        body: dict[str, Any] = {
            "query": query,
            "search_type": search_type,
            "jurisdiction": jurisdiction,
            "max_results": int(max_results),
        }
        if tenant_id.strip():
            body["tenant_id"] = tenant_id.strip()
        if filters_raw.strip():
            try:
                body["filters"] = json.loads(filters_raw)
            except json.JSONDecodeError as exc:
                st.error(f"Invalid filters JSON: {exc}")
                st.stop()

        with st.spinner("Searching…"):
            status, payload, error = _api(
                retrieval_url, "POST", "/tools/search", json_body=body
            )

        _show_response(status, payload, error)
        if status == 200 and isinstance(payload, dict):
            meta_cols = st.columns(4)
            meta_cols[0].metric("Results", payload.get("total_results", 0))
            meta_cols[1].metric("Time (ms)", payload.get("search_time_ms", "—"))
            meta_cols[2].metric("Degraded", "Yes" if payload.get("degraded") else "No")
            meta_cols[3].write(f"**request_id:** `{payload.get('request_id', '')}`")
            results = payload.get("results", [])
            st.session_state["last_search_results"] = results
            _render_search_results(results)

with tab_fetch:
    st.subheader("POST /tools/fetch_and_extract")
    last_results: list[dict[str, Any]] = st.session_state.get("last_search_results", [])
    pick_from_search = None
    if last_results:
        options = {
            f"[{r.get('source_type')}] {r.get('title', r.get('source_id'))}": r
            for r in last_results
        }
        pick_from_search = st.selectbox(
            "Pick from last search (optional)",
            ["— manual entry —", *options.keys()],
            key="fetch_pick",
        )
        if pick_from_search and pick_from_search != "— manual entry —":
            picked = options[pick_from_search]
            default_source_id = picked.get("source_id", "")
            default_source_type = picked.get("source_type", "web")
        else:
            default_source_id = ""
            default_source_type = "web"
    else:
        default_source_id = ""
        default_source_type = "web"
        st.info("Run a search first to pick a result, or enter source details manually.")

    fc1, fc2 = st.columns(2)
    source_id = fc1.text_input("source_id", value=default_source_id, key="fetch_source_id")
    source_type = fc2.selectbox(
        "source_type",
        ["web", "internal"],
        index=["web", "internal"].index(default_source_type)
        if default_source_type in ["web", "internal"]
        else 0,
        key="fetch_source_type",
    )
    extract_sections = st.text_input(
        "extract_sections (comma-separated, optional)",
        value="",
        key="fetch_extract_sections",
    )
    fetch_tenant = st.text_input(
        "tenant_id (for internal docs)", value="", key="fetch_tenant_id"
    )

    if st.button("Fetch document", type="primary", key="fetch_btn"):
        body = {
            "source_id": source_id,
            "source_type": source_type,
        }
        if extract_sections.strip():
            body["extract_sections"] = [
                s.strip() for s in extract_sections.split(",") if s.strip()
            ]
        if fetch_tenant.strip():
            body["tenant_id"] = fetch_tenant.strip()

        with st.spinner("Fetching…"):
            status, payload, error = _api(
                retrieval_url, "POST", "/tools/fetch_and_extract", json_body=body
            )

        _show_response(status, payload, error)
        if status == 200 and isinstance(payload, dict):
            st.markdown(f"### {payload.get('title', 'Document')}")
            if payload.get("url"):
                st.link_button("Open URL", payload["url"], key="fetch_open_url")
            st.caption(
                f"fetch_time_ms: {payload.get('fetch_time_ms')} · "
                f"sections: {len(payload.get('sections', []))}"
            )
            full_text = payload.get("full_text", "")
            if full_text:
                with st.expander("Full text", expanded=False):
                    st.text(full_text[:8000] + ("…" if len(full_text) > 8000 else ""))
            for section in payload.get("sections", []):
                with st.expander(section.get("title", section.get("section_id", "Section"))):
                    st.markdown(section.get("content", ""))

with tab_semantic:
    st.subheader("POST /tools/semantic_search")
    sem_query = st.text_area(
        "Query", value="arbitration clause enforceability", height=80, key="sem_query"
    )
    sc1, sc2, sc3 = st.columns(3)
    sem_search_type = sc1.selectbox(
        "search_type",
        ["all", "web", "internal"],
        key="sem_type",
    )
    top_k = sc2.number_input("top_k", min_value=1, max_value=100, value=10, key="sem_top_k")
    threshold = sc3.slider(
        "threshold", min_value=0.0, max_value=1.0, value=0.7, step=0.05, key="sem_threshold"
    )
    sem_tenant = st.text_input("tenant_id", value="", key="sem_tenant")

    if st.button("Run semantic search", type="primary", key="semantic_btn"):
        body: dict[str, Any] = {
            "query": sem_query,
            "search_type": sem_search_type,
            "top_k": int(top_k),
            "threshold": float(threshold),
        }
        if sem_tenant.strip():
            body["tenant_id"] = sem_tenant.strip()

        with st.spinner("Searching…"):
            status, payload, error = _api(
                retrieval_url, "POST", "/tools/semantic_search", json_body=body
            )

        _show_response(status, payload, error)
        if status == 200 and isinstance(payload, dict):
            if payload.get("stub"):
                st.warning(f"Stub response: {payload.get('stub_reason', 'Phase 2 not active')}")
            _render_search_results(payload.get("results", []))

with tab_citation:
    st.subheader("POST /tools/citation_graph")
    cc1, cc2 = st.columns(2)
    cite_source_id = cc1.text_input("source_id", value="", key="cite_source_id")
    cite_source_type = cc2.selectbox(
        "source_type",
        ["web", "internal"],
        key="cite_type",
    )
    cc3, cc4 = st.columns(2)
    depth = cc3.number_input("depth", min_value=1, max_value=5, value=1, key="cite_depth")
    direction = cc4.selectbox(
        "direction", ["both", "incoming", "outgoing"], key="cite_direction"
    )

    if st.button("Build citation graph", type="primary", key="cite_btn"):
        body = {
            "source_id": cite_source_id,
            "source_type": cite_source_type,
            "depth": int(depth),
            "direction": direction,
        }
        with st.spinner("Traversing graph…"):
            status, payload, error = _api(
                retrieval_url, "POST", "/tools/citation_graph", json_body=body
            )

        _show_response(status, payload, error)
        if status == 200 and isinstance(payload, dict):
            if payload.get("stub"):
                st.warning(f"Stub response: {payload.get('stub_reason', 'Phase 2 not active')}")
            nodes = payload.get("nodes", [])
            edges = payload.get("edges", [])
            st.metric("Nodes", len(nodes))
            st.metric("Edges", len(edges))
            if nodes:
                st.dataframe(nodes, use_container_width=True)
            if edges:
                st.dataframe(edges, use_container_width=True)

with tab_ingest:
    st.subheader("POST /tools/ingest_internal")
    ingest_tenant = st.text_input("tenant_id", value="demo-tenant", key="ingest_tenant")
    ingest_title = st.text_input("title", value="NDA Policy", key="ingest_title")
    ingest_text = st.text_area(
        "text",
        value="Confidentiality obligations apply for a period of two years after termination.",
        height=120,
        key="ingest_text",
    )
    ingest_source_id = st.text_input("source_id (optional)", value="", key="ingest_source_id")
    ingest_metadata = st.text_area(
        "metadata (JSON, optional)", value="", height=60, key="ingest_metadata"
    )

    if st.button("Ingest document", type="primary", key="ingest_btn"):
        body: dict[str, Any] = {
            "tenant_id": ingest_tenant,
            "title": ingest_title,
            "text": ingest_text,
        }
        if ingest_source_id.strip():
            body["source_id"] = ingest_source_id.strip()
        if ingest_metadata.strip():
            try:
                body["metadata"] = json.loads(ingest_metadata)
            except json.JSONDecodeError as exc:
                st.error(f"Invalid metadata JSON: {exc}")
                st.stop()

        with st.spinner("Ingesting…"):
            status, payload, error = _api(
                retrieval_url, "POST", "/tools/ingest_internal", json_body=body
            )

        _show_response(status, payload, error)
        if status == 200 and isinstance(payload, dict):
            st.success(
                f"Ingested `{payload.get('source_id')}` "
                f"({'deduped' if payload.get('deduped') else 'new'})"
            )
