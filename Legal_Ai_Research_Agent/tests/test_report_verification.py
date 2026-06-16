"""Tests for report verification gate, citation extraction, and caveats formatting."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from deep_research_from_scratch.report_verification import (
    _VerifierLLMOutput,
    deterministic_checks,
    finalize_report,
    route_after_verify,
    verify_report,
)
from deep_research_from_scratch.source_registry import RetrievedSource, extract_citations
from deep_research_from_scratch.state_scope import AgentState, VerificationResult


def test_extract_citations():
    """Verify regex citation extraction matches various Indian court citation formats."""
    text = (
        "As held in ABC v. XYZ, 2024 INSC 1, and also in "
        "PQR v. LMN (1973) 4 SCC 225. Further, check AIR 1973 SC 1461 and "
        "AIR 2010 Bom 12. Also, (2018) 5 SCC (Cri) 1 and 2023 SCC OnLine Del 45."
    )
    citations = extract_citations(text)
    
    # Assert normalized matches (whitespace collapsed, uppercase)
    assert "2024 INSC 1" in citations
    assert "(1973) 4 SCC 225" in citations
    assert "AIR 1973 SC 1461" in citations
    assert "AIR 2010 BOM 12" in citations
    assert "(2018) 5 SCC (CRI) 1" in citations
    assert "2023 SCC ONLINE DEL 45" in citations


def test_deterministic_checks():
    """Verify deterministic checks catch missing sections, missing disclaimer, and fabricated citations."""
    findings = "Governing precedent is Zaheer Khan, (2006) 4 SCC 227."
    sources = [
        RetrievedSource(
            url="https://indiankanoon.org/doc/zaheer/",
            title="Zaheer Khan",
            authority_tier="primary",
            fetched=True,
            excerpt="Zaheer Khan, (2006) 4 SCC 227",
        )
    ]

    # Correct report containing all sections and current citations
    correct_report = (
        "## Questions Presented\n"
        "## Brief Answer\n"
        "## Statement of Facts\n"
        "## Discussion\n"
        "## Practical Guidance\n"
        "## Conclusion\n"
        "## Table of Authorities\n"
        "## Disclaimer\n"
        "This is not legal advice. Based on Zaheer Khan, (2006) 4 SCC 227."
    )

    res = deterministic_checks(correct_report, findings, sources)
    assert res["passed"] is True
    assert len(res["fabricated"]) == 0
    assert len(res["missing_sections"]) == 0
    assert res["disclaimer_present"] is True

    # Report missing required sections and disclaimer
    bad_report_1 = "## Discussion\nBased on Zaheer Khan, (2006) 4 SCC 227."
    res = deterministic_checks(bad_report_1, findings, sources)
    assert res["passed"] is False
    assert "Questions Presented" in res["missing_sections"]
    assert res["disclaimer_present"] is False

    # Report containing fabricated citation (not in findings)
    fabricated_report = correct_report + "\nSee other case: AIR 1973 SC 1461."
    res = deterministic_checks(fabricated_report, findings, sources)
    assert res["passed"] is False
    assert "AIR 1973 SC 1461" in res["fabricated"]


def test_is_rate_limit_error():
    from deep_research_from_scratch.model_config import is_rate_limit_error

    assert is_rate_limit_error(Exception('Error 429: {"type":"rate_limited"}'))
    assert is_rate_limit_error(Exception("Rate limit exceeded"))
    assert not is_rate_limit_error(Exception("Invalid model"))


@pytest.mark.asyncio
async def test_verify_report_skips_llm_on_rate_limit(mock_llm):
    """Rate-limited verifier falls back to deterministic checks only."""
    state = AgentState(
        final_report=(
            "## Questions Presented\n## Brief Answer\n## Statement of Facts\n"
            "## Discussion\n## Practical Guidance\n## Conclusion\n## Table of Authorities\n## Disclaimer\n"
            "This is not legal advice. Based on Zaheer Khan, (2006) 4 SCC 227."
        ),
        notes=["Precedent is Zaheer Khan, (2006) 4 SCC 227."],
        raw_notes=[],
        retrieved_sources=[
            RetrievedSource(
                url="https://indiankanoon.org/doc/zaheer/",
                title="Zaheer Khan",
                authority_tier="primary",
                fetched=True,
                excerpt="Zaheer Khan, (2006) 4 SCC 227",
            )
        ],
        verification_retries=0,
    )

    with patch("deep_research_from_scratch.report_verification.app_config") as mock_cfg:
        mock_cfg.LLM_SKIP_VERIFIER = False
        with patch("deep_research_from_scratch.report_verification.get_chat_model") as mock_get_model:
            mock_structured = MagicMock()
            mock_structured.ainvoke = AsyncMock(
                side_effect=Exception(
                    'Error 429: {"message":"Rate limit exceeded","type":"rate_limited"}'
                )
            )
            mock_model = MagicMock()
            mock_model.bind.return_value.with_structured_output.return_value = mock_structured
            mock_model.with_structured_output.return_value = mock_structured
            mock_get_model.return_value = mock_model

            with patch(
                "deep_research_from_scratch.report_verification.ainvoke_with_retry",
                new=AsyncMock(
                    side_effect=Exception(
                        'Error 429: {"message":"Rate limit exceeded","type":"rate_limited"}'
                    )
                ),
            ):
                update = await verify_report(
                    state, config={"configurable": {"thread_id": "rate_limit"}}
                )

    assert update["verification"].passed is True
    assert "rate-limit" in update["verification"].overall_assessment.lower()


@pytest.mark.asyncio
async def test_verify_report_node(mock_llm):
    """Verify verify_report node invokes LLM reviewer and compiles verification details."""
    state = AgentState(
        final_report=(
            "## Questions Presented\n## Brief Answer\n## Statement of Facts\n"
            "## Discussion\n## Practical Guidance\n## Conclusion\n## Table of Authorities\n## Disclaimer\n"
            "This is not legal advice. Based on Zaheer Khan, (2006) 4 SCC 227."
        ),
        notes=["Precedent is Zaheer Khan, (2006) 4 SCC 227."],
        raw_notes=["Fetched: Zaheer Khan, (2006) 4 SCC 227."],
        retrieved_sources=[
            RetrievedSource(
                url="https://indiankanoon.org/doc/zaheer/",
                title="Zaheer Khan",
                authority_tier="primary",
                fetched=True,
                excerpt="Zaheer Khan, (2006) 4 SCC 227",
            )
        ],
        verification_retries=0,
    )
    
    # Set up mock verifier output
    mock_ver_out = _VerifierLLMOutput(
        passed=True,
        confidence="high",
        unsupported_claims=[],
        overstated_holdings=[],
        law_currency_issues=[],
        required_fixes="",
        overall_assessment="Looks good",
    )
    
    # Mock with_structured_output for the verifier model
    with patch("deep_research_from_scratch.report_verification.app_config") as mock_cfg:
        mock_cfg.LLM_SKIP_VERIFIER = False
        with patch("deep_research_from_scratch.report_verification.get_chat_model") as mock_get_model:
            mock_model_instance = MagicMock()
            mock_structured_model = MagicMock()
            mock_structured_model.ainvoke = AsyncMock(return_value=mock_ver_out)
            mock_model_instance.bind.return_value.with_structured_output.return_value = (
                mock_structured_model
            )
            mock_model_instance.with_structured_output.return_value = mock_structured_model
            mock_get_model.return_value = mock_model_instance

            update = await verify_report(state, config={"configurable": {"thread_id": "session123"}})
        
        assert "verification" in update
        assert isinstance(update["verification"], VerificationResult)
        assert update["verification"].passed is True
        assert update["verification_retries"] == 1


def test_route_after_bootstrap_fast_mode_skips_supervisor():
    from deep_research_from_scratch.research_agent_full import route_after_bootstrap

    with patch("deep_research_from_scratch.research_agent_full.app_config") as mock_cfg:
        mock_cfg.FAST_RESEARCH_MODE = True
        mock_cfg.FAST_MODE_MIN_FETCHES = 3
        state = AgentState(
            retrieved_sources=[
                RetrievedSource(
                    url=f"https://indiankanoon.org/doc/{i}/",
                    title=f"Case {i}",
                    authority_tier="primary",
                    fetched=True,
                )
                for i in range(3)
            ],
        )
        assert route_after_bootstrap(state) == "final_report_generation"


def test_route_after_bootstrap_falls_back_to_supervisor_without_fetches():
    from deep_research_from_scratch.research_agent_full import route_after_bootstrap

    with patch("deep_research_from_scratch.research_agent_full.app_config") as mock_cfg:
        mock_cfg.FAST_RESEARCH_MODE = True
        mock_cfg.FAST_MODE_MIN_FETCHES = 3
        state = AgentState(
            retrieved_sources=[
                RetrievedSource(
                    url="https://indiankanoon.org/doc/1/",
                    title="Case",
                    authority_tier="primary",
                    fetched=True,
                )
            ],
        )
        assert route_after_bootstrap(state) == "supervisor_subgraph"


def test_route_after_verify():
    """Verify routing decisions based on verification status and retry counts."""
    # Scenario 1: Verification passed
    state_pass = AgentState(
        verification=VerificationResult(passed=True),
        verification_retries=1,
    )
    assert route_after_verify(state_pass) == "finalize_report"

    # Scenario 2: Verification failed, retries under limit (limit=2)
    state_fail_retry = AgentState(
        verification=VerificationResult(passed=False),
        verification_retries=1,
    )
    assert route_after_verify(state_fail_retry) == "final_report_generation"

    # Scenario 3: Verification failed, retries exceeded limit (default MAX_REVIEWER_RETRIES=3)
    state_fail_force = AgentState(
        verification=VerificationResult(passed=False),
        verification_retries=4,
    )
    assert route_after_verify(state_fail_force) == "finalize_report"


def test_deterministic_checks_uses_raw_notes_and_sources():
    """Verification corpus includes raw notes and structured fetched sources."""
    findings = "Virsa Singh v State of Punjab, (1958) SCR 149."
    report = (
        "## Questions Presented\n## Brief Answer\n## Statement of Facts\n"
        "## Discussion\nAs held in Virsa Singh v State of Punjab.\n"
        "## Practical Guidance\n## Conclusion\n## Table of Authorities\n## Disclaimer\n"
        "This is not legal advice. See [1].\n\n### Sources\n"
        "[1] Virsa Singh: https://indiankanoon.org/doc/1/\n"
    )
    sources = [
        RetrievedSource(
            url="https://indiankanoon.org/doc/1/",
            title="Virsa Singh",
            authority_tier="primary",
            fetched=True,
            excerpt="Virsa Singh v State of Punjab",
        )
    ]
    res = deterministic_checks(report, findings, sources)
    assert res["passed"] is True


def test_deterministic_checks_flags_crypto_landmark_gap():
    findings = "Some generic banking case about accounts."
    report = (
        "## Questions Presented\n## Brief Answer\n## Statement of Facts\n"
        "## Discussion\nCryptocurrency is regulated.\n"
        "## Practical Guidance\n## Conclusion\n## Table of Authorities\n## Disclaimer\n"
        "This is not legal advice.\n"
    )
    res = deterministic_checks(
        report,
        findings,
        [],
        research_brief="Research cryptocurrency regulation and PMLA enforcement in India",
    )
    assert res["passed"] is False
    assert any("IAMAI" in item for item in res["missing_landmarks"])


def test_deterministic_checks_flags_access_denied_urls():
    findings = "Zaheer Khan, (2006) 4 SCC 227."
    report = (
        "## Questions Presented\n## Brief Answer\n## Statement of Facts\n"
        "## Discussion\nSee [1].\n## Practical Guidance\n## Conclusion\n"
        "## Table of Authorities\n## Disclaimer\nThis is not legal advice.\n\n"
        "### Sources\n[1] Paywalled: https://www.manupatra.com/doc/1/\n"
    )
    sources = [
        RetrievedSource(
            url="https://indiankanoon.org/doc/1/",
            title="Zaheer Khan",
            authority_tier="primary",
            fetched=True,
            excerpt="Zaheer Khan",
        )
    ]
    res = deterministic_checks(report, findings, sources)
    assert res["passed"] is False
    assert res["access_denied_urls"]


def test_finalize_report_redacts_fabricated_citations():
    """Fabricated citations are redacted before caveats are appended."""
    state = AgentState(
        final_report="Based on AIR 2024 SC 99, the rule is clear.",
        verification=VerificationResult(
            passed=False,
            fabricated_or_unverified_citations=["AIR 2024 SC 99"],
            overall_assessment="Fabricated citation.",
        ),
    )

    update = finalize_report(state, config={"configurable": {"thread_id": "session_redact"}})
    final_text = update["final_report"]
    memo_body = final_text.split("## Verification Caveats")[0]

    assert "AIR 2024 SC 99" not in memo_body
    assert "CITATION REMOVED" in memo_body
    assert "Verification Caveats" in final_text


def test_finalize_report_with_caveats():
    """Verify finalize_report node appends caveats section when verification has failed."""
    state = AgentState(
        final_report="## Discussion\nThis is a memo.",
        verification=VerificationResult(
            passed=False,
            fabricated_or_unverified_citations=["AIR 2024 SC 99"],
            unsupported_claims=["Claim about Section 27 Contract Act"],
            overstated_holdings=["Zaheer Khan holds post-employment clauses are valid"],
            law_currency_issues=["Old IPC section cited for offence after July 2024"],
            missing_sections=["Statement of Facts"],
            overall_assessment="Critical issues flagged.",
        ),
    )
    
    update = finalize_report(state, config={"configurable": {"thread_id": "session_caveat"}})
    final_text = update["final_report"]
    
    assert "Verification Caveats" in final_text
    assert "AIR 2024 SC 99" in final_text
    assert "Claim about Section 27 Contract Act" in final_text
    assert "Zaheer Khan holds post-employment clauses are valid" in final_text
    assert "Old IPC section cited" in final_text
    assert "Statement of Facts" in final_text
    assert "Critical issues flagged" in final_text
