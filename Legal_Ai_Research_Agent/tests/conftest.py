"""Shared test fixtures and environment configuration for Deep Research tests.

This module initializes the test environment, mock setups, and common fixtures
prior to test module collection.
"""

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ===== 1. BOOTSTRAP ENVIRONMENT & PRE-COLLECTION PATCHING =====
# Establish a temporary memory directory for import-time configurations.
_initial_temp_dir = tempfile.mkdtemp()
os.environ["DEEP_RESEARCH_MEMORY_DIR"] = _initial_temp_dir

# Start the mock patch for init_chat_model immediately before importing project code
_patcher = patch("deep_research_from_scratch.model_config.init_chat_model")
mock_init_chat_model = _patcher.start()

def pytest_unconfigure(config):
    """Cleanup module-level patches after all tests complete."""
    _patcher.stop()

# ===== 2. LOCAL MODULE IMPORTS =====
from langchain_core.messages import AIMessage, HumanMessage

from deep_research_from_scratch.state_multi_agent_supervisor import SupervisorState
from deep_research_from_scratch.state_scope import AgentState

# ===== 3. MOCK CHAT MODEL WRAPPER =====

class MockChatModel:
    """Mock Chat Model supporting invoke, ainvoke and structured output schemas."""
    
    def __init__(self):
        self.invoke = MagicMock()
        self.ainvoke = AsyncMock()
        self.bind_tools = MagicMock(return_value=self)
        
        # Setup default mock responses
        mock_msg = MagicMock(spec=AIMessage)
        mock_msg.content = "Mock LLM text response"
        mock_msg.tool_calls = []
        self.invoke.return_value = mock_msg
        
        mock_amsg = MagicMock(spec=AIMessage)
        mock_amsg.content = "Mock async LLM text response"
        mock_amsg.tool_calls = []
        self.ainvoke.return_value = mock_amsg

    def with_structured_output(self, schema, **kwargs):
        """Mock structured output generation dynamically populating fields of Pydantic schemas."""
        from pydantic import BaseModel
        
        # Determine the structure to return
        if issubclass(schema, BaseModel):
            data = {}
            for field_name, field_info in schema.model_fields.items():
                ann = field_info.annotation
                if ann is bool:
                    # Provide defaults that let nodes run without throwing schema errors
                    data[field_name] = False
                elif ann is int:
                    data[field_name] = 0
                elif ann is float:
                    data[field_name] = 0.0
                elif ann is str:
                    # Tailor strings to satisfy validations or look realistic
                    if field_name == "research_brief":
                        data[field_name] = (
                            "This is a mock research brief that has more than fifty "
                            "characters to pass any potential validations."
                        )
                    elif field_name == "question":
                        data[field_name] = "Mock clarifying question?"
                    elif field_name == "verification":
                        data[field_name] = "Mock verification message."
                    elif field_name == "summary":
                        data[field_name] = "Mock webpage summary content."
                    elif field_name == "key_excerpts":
                        data[field_name] = "Mock excerpt 1; Mock excerpt 2"
                    else:
                        data[field_name] = "Mock text output"
                elif hasattr(ann, "__origin__") and ann.__origin__ is list:
                    data[field_name] = []
                else:
                    data[field_name] = None
            
            mock_instance = schema(**data)
        else:
            mock_instance = MagicMock()

        wrapped_invoke = MagicMock(return_value=mock_instance)
        wrapped_ainvoke = AsyncMock(return_value=mock_instance)
        
        mock_wrapped_model = MagicMock()
        mock_wrapped_model.invoke = wrapped_invoke
        mock_wrapped_model.ainvoke = wrapped_ainvoke
        return mock_wrapped_model

# Instruct init_chat_model mock to return our MockChatModel instances
mock_init_chat_model.return_value = MockChatModel()


# ===== 4. SHARED FIXTURES =====

@pytest.fixture
def mock_llm():
    """Fixture to obtain a MockChatModel instance in tests."""
    return MockChatModel()

@pytest.fixture(autouse=True)
def configure_test_memory_dir(tmp_path, monkeypatch):
    """Automatically isolate the memory directory for each test to prevent cross-contamination."""
    test_dir = tmp_path / "memory_test"
    test_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DEEP_RESEARCH_MEMORY_DIR", str(test_dir))
    return test_dir

@pytest.fixture
def simple_agent_state():
    """Create a minimal AgentState with a single user message."""
    return AgentState(messages=[HumanMessage(content="Hello")])

@pytest.fixture
def legal_agent_state():
    """Create an AgentState with a realistic legal research request."""
    return AgentState(
        messages=[
            HumanMessage(
                content=(
                    "I need to research whether my employer can enforce "
                    "a non-compete agreement in India. I signed it "
                    "when I was hired in 2023 and I want to leave to join "
                    "a competitor."
                )
            )
        ]
    )

@pytest.fixture
def supervisor_state():
    """Create a minimal SupervisorState."""
    return SupervisorState(
        supervisor_messages=[],
        research_brief="Test research brief",
        notes=[],
        research_iterations=0,
        raw_notes=[],
    )

@pytest.fixture
def sample_research_brief():
    """Return a sample legal research brief for testing."""
    return (
        "LEGAL RESEARCH BRIEF\n\n"
        "JURISDICTION: Supreme Court of India / Delhi High Court\n"
        "PRACTICE AREA: Restraint of Trade under Section 27 of the Indian Contract Act, 1872\n\n"
        "LEGAL ISSUES:\n"
        "1. Whether Section 27 of the Indian Contract Act, 1872 renders "
        "the employer's post-employment non-compete agreement void.\n"
        "2. Whether covenants in restraint of trade during employment are enforceable.\n\n"
        "FACT PATTERN:\n"
        "Employee signed a non-compete agreement upon hire in 2023 in Delhi. "
        "Employee now wishes to leave to join a direct competitor.\n\n"
        "USER OBJECTIVE: Determine enforceability under Indian law and advise on risk.\n\n"
        "OPEN PARAMETERS: [UNSPECIFIED - Exact contract language]"
    )

@pytest.fixture
def sample_research_findings():
    """Return sample compressed research findings for testing."""
    return [
        "[HIGH CONFIDENCE] Section 27 of the Indian Contract Act, 1872 states: "
        "'Every agreement by which any one is restrained from exercising a "
        "lawful profession, trade or business of any kind, is to that extent void.'",
        "[HIGH CONFIDENCE] Percept D'Mark (India) Pvt. Ltd. v. Zaheer Khan, (2006) 4 SCC 227: "
        "The Supreme Court of India held that post-employment non-compete covenants are "
        "completely void and unenforceable under Section 27, whereas negative covenants "
        "operative during the term of employment are generally valid.",
    ]
