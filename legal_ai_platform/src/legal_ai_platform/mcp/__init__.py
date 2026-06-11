"""MCP client layer.

Current:
    RetrievalMCPClient  — HTTP client to the Legal ai retrieval server

Future extension points (not implemented):
    DocumentMCPClient     — OCR, redlining, redaction
    ReasoningMCPClient    — LLM calls, citation verification
    ActionMCPClient       — drafts, e-signatures, calendaring
"""

from legal_ai_platform.mcp.base_client import BaseMCPClient
from legal_ai_platform.mcp.retrieval_client import RetrievalMCPClient

__all__ = ["BaseMCPClient", "RetrievalMCPClient"]
