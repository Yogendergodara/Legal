"""Streamlit chatbot UI for the Legal AI Research Agent."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any

import httpx
import streamlit as st
import streamlit.components.v1 as components

DEFAULT_PLATFORM_URL = "http://localhost:8080"
TIMEOUT_SECONDS = int(os.environ.get("STREAMLIT_QUERY_TIMEOUT", "900"))
WAIT_MESSAGE = "Researching… this can take a few minutes."

st.set_page_config(
    page_title="Legal AI Research",
    page_icon="⚖️",
    layout="centered",
)


# ── HTTP helper ───────────────────────────────────────────────────────────────

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
        return None, None, f"Cannot connect to {url}. Is the platform gateway running?"
    except httpx.TimeoutException:
        return None, None, f"Request timed out after {timeout}s."
    except httpx.HTTPError as exc:
        return None, None, str(exc)


# ── Session state ─────────────────────────────────────────────────────────────

def _init_state() -> None:
    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("thread_id", None)
    st.session_state.setdefault("awaiting_input", False)
    st.session_state.setdefault("suggested_followups", [])
    st.session_state.setdefault("pending_followup", None)
    st.session_state.setdefault("research_directions", [])


def _reset_chat() -> None:
    st.session_state["messages"] = []
    st.session_state["thread_id"] = None
    st.session_state["awaiting_input"] = False
    st.session_state["suggested_followups"] = []
    st.session_state["pending_followup"] = None
    st.session_state["research_directions"] = []


# ── Follow-up question parsing ────────────────────────────────────────────────

def _parse_followup_questions(text: str) -> list[str]:
    """Extract numbered follow-up questions from the Suggested Follow-up Queries section."""
    questions: list[str] = []
    in_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"#+\s*suggested follow.up", stripped, re.IGNORECASE):
            in_section = True
            continue
        if in_section and re.match(r"^#{1,3}\s", stripped) and not re.match(r"#+\s*suggested follow.up", stripped, re.IGNORECASE):
            break
        if in_section:
            m = re.match(r"^(\d+)[.)]\s+(.+)", stripped)
            if m:
                question = m.group(2).strip()
                if len(question) > 15:
                    questions.append(question)
    return questions[:5]


# ── Query execution ───────────────────────────────────────────────────────────

def _run_query(platform_url: str, prompt: str) -> None:
    body: dict[str, Any] = {
        "query": prompt,
        "task_type": "research",
        "max_results": 10,
    }
    if st.session_state.get("thread_id"):
        body["thread_id"] = st.session_state["thread_id"]

    status, payload, error = _api(
        platform_url, "POST", "/query", json_body=body, timeout=TIMEOUT_SECONDS
    )

    if error:
        return _push_assistant(f"⚠️ {error}", success=False)
    if status != 200 or not isinstance(payload, dict):
        return _push_assistant(f"⚠️ Unexpected response (HTTP {status}).", success=False)

    if payload.get("thread_id"):
        st.session_state["thread_id"] = payload["thread_id"]
    st.session_state["awaiting_input"] = bool(payload.get("awaiting_input"))
    st.session_state["research_directions"] = payload.get("research_directions") or []

    output = (payload.get("output") or "").strip()
    if payload.get("awaiting_input"):
        text = output or "Could you share a bit more detail so I can refine the research?"
        _push_assistant(text, success=True, clarifying=True, meta=payload)
    elif payload.get("success"):
        text = output or (
            "The request completed but returned no text. Check the gateway logs."
        )
        _push_assistant(text, success=True, meta=payload)
    else:
        text = payload.get("error") or "Research failed."
        _push_assistant(f"⚠️ {text}", success=False, meta=payload)


def _push_assistant(
    text: str,
    *,
    success: bool,
    clarifying: bool = False,
    meta: dict[str, Any] | None = None,
) -> None:
    st.session_state["messages"].append(
        {
            "role": "assistant",
            "content": text,
            "success": success,
            "clarifying": clarifying,
            "meta": meta or {},
        }
    )
    if success and not clarifying:
        followups = _parse_followup_questions(text)
        st.session_state["suggested_followups"] = followups
    elif clarifying:
        st.session_state["suggested_followups"] = []


# ── Export helpers ────────────────────────────────────────────────────────────

def _format_conversation_markdown(messages: list[dict[str, Any]]) -> str:
    parts = ["# Legal Research Conversation\n"]
    for message in messages:
        role = "You" if message["role"] == "user" else "Assistant"
        parts.append(f"## {role}\n\n{message.get('content', '').strip()}\n")
    parts.append(
        f"\n---\n*Exported {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*"
    )
    return "\n".join(parts)


def _render_research_actions(content: str, action_key: str) -> None:
    """Download and copy buttons for a completed research report."""
    text = content.strip()
    if not text:
        return

    col_download, col_copy = st.columns(2)
    with col_download:
        st.download_button(
            label="Download research",
            data=text,
            file_name=f"legal_research_{action_key}.md",
            mime="text/markdown",
            key=f"download_{action_key}",
            use_container_width=True,
        )
    with col_copy:
        escaped = json.dumps(text)
        components.html(
            f"""
            <button id="copy-{action_key}" style="
                width: 100%;
                padding: 0.45rem 0.75rem;
                border: 1px solid rgba(49, 51, 63, 0.2);
                border-radius: 0.5rem;
                background: rgb(255, 255, 255);
                color: rgb(49, 51, 63);
                cursor: pointer;
                font-size: 0.875rem;
                font-family: 'Source Sans Pro', sans-serif;
            ">Copy research</button>
            <script>
            document.getElementById("copy-{action_key}").addEventListener("click", function() {{
                navigator.clipboard.writeText({escaped}).then(function() {{
                    var btn = document.getElementById("copy-{action_key}");
                    btn.innerText = "Copied!";
                    setTimeout(function() {{ btn.innerText = "Copy research"; }}, 2000);
                }});
            }});
            </script>
            """,
            height=42,
        )


# ── Assistant message extras ──────────────────────────────────────────────────

def _render_assistant_extras(message: dict[str, Any]) -> None:
    meta = message.get("meta") or {}
    if not meta:
        return
    bits = []
    if meta.get("agent"):
        bits.append(f"**agent:** {meta['agent']}")
    if meta.get("task_type"):
        bits.append(f"**task_type:** {meta['task_type']}")
    if bits:
        st.caption(" · ".join(bits))
    if meta.get("artifacts"):
        with st.expander("Artifacts"):
            st.json(meta["artifacts"])
    if meta.get("events"):
        with st.expander("Events"):
            st.json(meta["events"])


# ── App layout ────────────────────────────────────────────────────────────────

_init_state()

with st.sidebar:
    st.header("⚖️ Legal AI Research")
    platform_url = st.text_input(
        "Platform gateway URL",
        value=DEFAULT_PLATFORM_URL,
        key="platform_url",
    )

    if st.button("Check health", use_container_width=True, key="health_btn"):
        status, payload, error = _api(platform_url, "GET", "/health", timeout=10)
        if error:
            st.error(error)
        elif status == 200 and isinstance(payload, dict):
            st.success(
                f"{payload.get('service')} v{payload.get('version')} — "
                f"{payload.get('status')}"
            )
        else:
            st.warning(f"Unexpected response (HTTP {status})")

    if st.button("🗑️ New chat", use_container_width=True, key="new_chat_btn"):
        _reset_chat()
        st.rerun()

    messages = st.session_state.get("messages", [])
    if messages:
        st.divider()
        st.caption("Export this session")
        conversation_md = _format_conversation_markdown(messages)
        st.download_button(
            label="Download conversation",
            data=conversation_md,
            file_name="legal_research_conversation.md",
            mime="text/markdown",
            key="download_conversation",
            use_container_width=True,
        )
        components.html(
            f"""
            <button id="copy-conversation" style="
                width: 100%;
                padding: 0.45rem 0.75rem;
                border: 1px solid rgba(49, 51, 63, 0.2);
                border-radius: 0.5rem;
                background: rgb(255, 255, 255);
                color: rgb(49, 51, 63);
                cursor: pointer;
                font-size: 0.875rem;
                font-family: 'Source Sans Pro', sans-serif;
            ">Copy conversation</button>
            <script>
            document.getElementById("copy-conversation").addEventListener("click", function() {{
                navigator.clipboard.writeText({json.dumps(conversation_md)}).then(function() {{
                    var btn = document.getElementById("copy-conversation");
                    btn.innerText = "Copied!";
                    setTimeout(function() {{ btn.innerText = "Copy conversation"; }}, 2000);
                }});
            }});
            </script>
            """,
            height=42,
        )

    st.divider()
    if st.session_state.get("thread_id"):
        st.caption(f"Session: `{st.session_state['thread_id']}`")
    st.caption(
        "Start the platform gateway with:\n\n"
        "```\nuvicorn legal_ai_platform.gateway.app:app --port 8080\n```"
    )

st.title("Legal Research Assistant")

if not st.session_state["messages"]:
    st.caption(
        "Ask a legal research question and I'll investigate using the research agent. "
        "I'll keep context across follow-up questions."
    )

# ── Render existing messages ──────────────────────────────────────────────────

for idx, message in enumerate(st.session_state["messages"]):
    avatar = "🧑" if message["role"] == "user" else "⚖️"
    with st.chat_message(message["role"], avatar=avatar):
        st.markdown(message["content"])
        if message["role"] == "assistant":
            _render_assistant_extras(message)
            if message.get("success") and not message.get("clarifying"):
                _render_research_actions(message["content"], f"msg_{idx}")

# ── Handle pending follow-up (submitted via button click) ────────────────────

if st.session_state.get("pending_followup"):
    followup_q = st.session_state.pop("pending_followup")
    st.session_state["suggested_followups"] = []
    st.session_state["research_directions"] = []
    st.session_state["messages"].append({"role": "user", "content": followup_q})
    with st.chat_message("user", avatar="🧑"):
        st.markdown(followup_q)
    with st.chat_message("assistant", avatar="⚖️"):
        with st.spinner(WAIT_MESSAGE):
            _run_query(platform_url, followup_q)
    st.rerun()

# ── Research direction buttons (pre-research scoping) ─────────────────────────

directions = st.session_state.get("research_directions", [])
if directions and st.session_state.get("awaiting_input"):
    st.markdown("---")
    st.markdown("**📍 Select a research direction** *(click to proceed)*")
    for idx, direction in enumerate(directions):
        label = direction if len(direction) <= 90 else direction[:87] + "…"
        if st.button(f"→ {label}", key=f"dir_{idx}", use_container_width=True):
            st.session_state["pending_followup"] = direction
            st.session_state["research_directions"] = []
            st.rerun()
    st.markdown("---")

# ── Suggested follow-up question buttons ─────────────────────────────────────

followups = st.session_state.get("suggested_followups", [])
if followups and not st.session_state.get("awaiting_input"):
    st.markdown("---")
    st.markdown("**💡 Suggested follow-up questions** *(click to research)*")
    for idx, question in enumerate(followups):
        label = question if len(question) <= 90 else question[:87] + "…"
        if st.button(f"→ {label}", key=f"fq_{idx}", use_container_width=True):
            st.session_state["pending_followup"] = question
            st.rerun()
    st.markdown("---")

# ── Chat input ────────────────────────────────────────────────────────────────

placeholder = (
    "Or type your own direction / add details…"
    if st.session_state.get("awaiting_input")
    else "Ask a legal research question…"
)

if prompt := st.chat_input(placeholder):
    st.session_state["suggested_followups"] = []
    st.session_state["research_directions"] = []
    st.session_state["messages"].append({"role": "user", "content": prompt})
    with st.chat_message("user", avatar="🧑"):
        st.markdown(prompt)

    with st.chat_message("assistant", avatar="⚖️"):
        with st.spinner(WAIT_MESSAGE):
            _run_query(platform_url, prompt)
    st.rerun()
