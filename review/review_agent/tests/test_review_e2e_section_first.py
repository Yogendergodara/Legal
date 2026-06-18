"""E2E section-first pipeline with mocked compare LLM."""

import pytest
from httpx import ASGITransport, AsyncClient

from document_core.schemas.compliance import ComplianceStatus, Severity
from mcp.document_server.main import app
from review_agent.clients.document_client import DocumentMCPClient
from review_agent.config import get_settings
from review_agent.graph.review_graph import run_review
from review_agent.schemas.section_compare import BatchSectionCompareLLMResult, SectionCompareItem
from review_agent.services import section_compare_llm
from tests.fixtures import SAMPLE_CONTRACT, SAMPLE_POLICY


@pytest.mark.asyncio
async def test_review_graph_section_first_e2e(monkeypatch):
    monkeypatch.setenv("REVIEW_PIPELINE_MODE", "section_first")
    monkeypatch.setenv("SECTION_CLASSIFY_MODE", "lexical")
    get_settings.cache_clear()

    async def _fake_invoke(_model, schema, *, system, user):
        assert schema is BatchSectionCompareLLMResult
        return BatchSectionCompareLLMResult(
            items=[
                SectionCompareItem(
                    section_id="12.2",
                    policy_document_id="",
                    policy_section_id="4",
                    dimension_label="Limitation of Liability",
                    status=ComplianceStatus.NON_COMPLIANT,
                    severity=Severity.CRITICAL,
                    contract_quote="total liability of either party",
                    policy_quote="fees paid in the twelve (12) months",
                    rationale="Contract liability section differs from policy cap language.",
                    confidence=0.85,
                )
            ]
        )

    monkeypatch.setattr(section_compare_llm, "get_review_model", lambda **_: object())
    monkeypatch.setattr(section_compare_llm, "invoke_structured", _fake_invoke)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        client = DocumentMCPClient("http://test", http_client=http)
        result = await run_review(
            client=client,
            tenant_id="demo",
            contract_text=SAMPLE_CONTRACT,
            contract_title="Vendor MSA",
            policy_texts=[{"title": "Vendor Policy", "text": SAMPLE_POLICY}],
            contract_type="msa",
        )

    assert result.get("section_retrieval_by_id")
    stats = result.get("compliance_stats") or {}
    assert stats.get("compliance_mode") == "section_first"
    report = result["report"]
    assert report is not None
    assert report.metadata.get("review_pipeline_mode") == "section_first"
    assert report.findings
