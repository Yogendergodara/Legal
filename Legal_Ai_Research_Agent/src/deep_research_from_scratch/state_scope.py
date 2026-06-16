
"""State Definitions and Pydantic Schemas for Research Scoping.

This defines the state objects and structured schemas used for
the research agent scoping workflow, including researcher state management and output schemas.
"""

import operator

from langchain_core.messages import BaseMessage
from langgraph.graph import MessagesState
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field
from typing_extensions import Annotated, List, Literal, Optional, Sequence

from deep_research_from_scratch.source_registry import RetrievedSource, merge_retrieved_sources

# ===== STATE DEFINITIONS =====

class AgentInputState(MessagesState):
    """Input state for the full agent - only contains messages from user input."""
    pass

class AgentState(MessagesState):
    """Main state for the full multi-agent research system.

    Extends MessagesState with additional fields for research coordination.
    Note: Some fields are duplicated across different state classes for proper
    state management between subgraphs and the main workflow.
    """

    # Research brief generated from user conversation history
    research_brief: str | None
    # Messages exchanged with the supervisor agent for coordination
    supervisor_messages: Annotated[Sequence[BaseMessage], add_messages]
    # Raw unprocessed research notes collected during the research phase
    raw_notes: Annotated[list[str], operator.add] = []
    # Processed and structured notes ready for report generation
    notes: Annotated[list[str], operator.add] = []
    # Final formatted research report
    final_report: str
    # Result of the report verification gate (anti-hallucination reviewer)
    verification: Optional["VerificationResult"]
    # How many times the report has been verified (bounds the revise loop)
    verification_retries: int = 0
    # Structured sources fetched during research (for verification + API)
    retrieved_sources: Annotated[list[RetrievedSource], merge_retrieved_sources] = []
    # Research directions suggested to user before research starts (transient, one-turn)
    research_directions: list[str] = []

# ===== STRUCTURED OUTPUT SCHEMAS =====

class SuggestDirections(BaseModel):
    """Schema for the pre-research direction selection / clarification gate."""

    action: Literal["suggest_directions", "ask_clarification", "proceed"] = Field(
        description=(
            "suggest_directions: present 3–4 research angles as clickable options. "
            "ask_clarification: ask ONE focused fact question (e.g. offence date, jurisdiction). "
            "proceed: start research immediately."
        )
    )
    research_directions: list[str] = Field(
        default_factory=list,
        description="3–4 concrete research angles when action == suggest_directions. Empty otherwise.",
    )
    direction_context: str = Field(
        default="",
        description="Preamble shown above direction options, e.g. 'I can research this from these angles:'",
    )
    clarification_question: str = Field(
        default="",
        description="ONE focused question when action == ask_clarification. Empty otherwise.",
    )
    verification: str = Field(
        default="",
        description="Brief acknowledgement when action == proceed. Empty otherwise.",
    )

class ResearchQuestion(BaseModel):
    """Schema for structured research brief generation."""

    research_brief: str = Field(
        description="A research question that will be used to guide the research.",
    )


class VerificationResult(BaseModel):
    """Result of the report verification gate.

    Merges a deterministic citation/structure check with an LLM grounding
    review. Stored in ``AgentState.verification`` and used to decide whether to
    ship the memo, revise it, or ship it with visible caveats.
    """

    passed: bool = Field(
        description="True only if the memo is fully grounded and well-formed."
    )
    confidence: Literal["high", "medium", "low"] = Field(
        default="medium",
        description="Reviewer confidence in this assessment.",
    )
    fabricated_or_unverified_citations: List[str] = Field(
        default_factory=list,
        description="Case citations in the memo that do not appear in the Findings.",
    )
    unsupported_claims: List[str] = Field(
        default_factory=list,
        description="Legal propositions in the memo not supported by the Findings.",
    )
    overstated_holdings: List[str] = Field(
        default_factory=list,
        description="Holdings stretched beyond what the cited authority actually decided.",
    )
    law_currency_issues: List[str] = Field(
        default_factory=list,
        description="Old-vs-new law errors (IPC/CrPC/Evidence Act vs BNS/BNSS/BSA by date).",
    )
    missing_sections: List[str] = Field(
        default_factory=list,
        description="Required IRAC memorandum sections that are absent.",
    )
    disclaimer_present: bool = Field(
        default=True,
        description="Whether the required AI-assistance disclaimer is present.",
    )
    required_fixes: str = Field(
        default="",
        description="Actionable, specific instructions the writer must apply on revision.",
    )
    overall_assessment: str = Field(
        default="",
        description="Short narrative summary of the review.",
    )
