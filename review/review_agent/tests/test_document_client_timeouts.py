"""MCP client per-path timeout mapping."""

from review_agent.clients.document_client import DocumentMCPClient


def test_sync_policies_uses_extended_timeout() -> None:
    client = DocumentMCPClient("http://localhost:8003")
    assert client._timeout_for("/tools/sync_policies") == 900.0
