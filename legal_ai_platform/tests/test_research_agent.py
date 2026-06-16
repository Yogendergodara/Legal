"""Tests for ResearchAgent session threading and timeout handling.

The LangGraph graph is replaced with a fake so no real LLM/HTTP calls happen.
"""

import asyncio

import pytest
from langchain_core.messages import AIMessage

from legal_ai_platform.agents.research.research_agent import ResearchAgent
from legal_ai_platform.models.agent import AgentRequest


class _FakeGraph:
    def __init__(self, state, delay: float = 0.0):
        self._state = state
        self._delay = delay
        self.last_config = None

    async def ainvoke(self, _input, config=None):
        self.last_config = config
        if self._delay:
            await asyncio.sleep(self._delay)
        return self._state


def _make_agent(state, delay: float = 0.0, timeout: float | None = None) -> ResearchAgent:
    agent = ResearchAgent(retrieval_client=object(), timeout_seconds=timeout)
    agent._graph = _FakeGraph(state, delay=delay)
    return agent


@pytest.mark.asyncio
async def test_completed_report_sets_thread_and_not_awaiting():
    agent = _make_agent({"final_report": "# Memo\nFindings.", "messages": []})
    response = await agent.execute(AgentRequest(query="limitation period?"))
    assert response.success is True
    assert response.output.startswith("# Memo")
    assert response.awaiting_input is False
    assert response.thread_id  # generated


@pytest.mark.asyncio
async def test_clarification_surfaces_question_and_awaiting_input():
    state = {"messages": [AIMessage(content="Which jurisdiction and contract type?")]}
    agent = _make_agent(state)
    response = await agent.execute(AgentRequest(query="contract help"))
    assert response.awaiting_input is True
    assert "jurisdiction" in response.output


@pytest.mark.asyncio
async def test_research_directions_surfaces_awaiting_input():
    state = {
        "messages": [AIMessage(content="I can research this from these angles:\n\n1. Option A")],
        "research_directions": [
            "IAMAI v RBI — RBI cryptocurrency restrictions",
            "PMLA enforcement on virtual digital assets",
        ],
    }
    agent = _make_agent(state)
    response = await agent.execute(AgentRequest(query="cryptocurrency regulation"))
    assert response.awaiting_input is True
    assert len(response.research_directions) == 2
    assert "IAMAI" in response.research_directions[0]


@pytest.mark.asyncio
async def test_thread_id_and_tenant_id_passed_to_graph():
    agent = _make_agent({"final_report": "ok", "messages": []})
    response = await agent.execute(
        AgentRequest(query="follow up", thread_id="session-123", tenant_id="tenant-abc")
    )
    assert response.thread_id == "session-123"
    assert agent._graph.last_config == {
        "configurable": {"thread_id": "session-123", "tenant_id": "tenant-abc"}
    }


@pytest.mark.asyncio
async def test_sources_populated_from_state():
    state = {
        "final_report": "# Memo",
        "messages": [],
        "retrieved_sources": [
            {
                "url": "https://indiankanoon.org/doc/1/",
                "title": "Test Case",
                "authority_tier": "primary",
                "fetched": True,
                "citation": "2024 INSC 1",
                "excerpt": "Ratio text",
                "source_type": "indiankanoon",
            }
        ],
    }
    agent = _make_agent(state)
    response = await agent.execute(AgentRequest(query="case law?"))
    research = response.artifacts["research"]
    assert len(research["sources"]) == 1
    assert research["sources"][0]["url"] == "https://indiankanoon.org/doc/1/"
    assert research["sources"][0]["metadata"]["fetched"] is True


@pytest.mark.asyncio
async def test_thread_id_is_preserved_and_passed_to_graph():
    agent = _make_agent({"final_report": "ok", "messages": []})
    response = await agent.execute(
        AgentRequest(query="follow up", thread_id="session-123")
    )
    assert response.thread_id == "session-123"
    assert agent._graph.last_config == {
        "configurable": {"thread_id": "session-123", "tenant_id": None}
    }


@pytest.mark.asyncio
async def test_timeout_returns_error_response():
    agent = _make_agent({"final_report": "late"}, delay=0.2, timeout=0.01)
    response = await agent.execute(AgentRequest(query="slow query"))
    assert response.success is False
    assert "timed out" in (response.error or "")
    assert response.thread_id  # still returned so the client can retry/continue
