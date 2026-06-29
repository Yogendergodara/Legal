"""Tests for quote validation helpers."""

from document_core.schemas.compliance import ComplianceStatus, Severity
from review_agent.schemas.compliance_llm import ComplianceLLMResult
from review_agent.services.quote_validate import (
    allows_empty_policy_quote,
    anchor_quote_in_haystack,
    quote_is_substring,
    truncate_section,
    validate_and_normalize_quotes,
)


def test_truncate_section_adds_marker():
    text = "word " * 5000
    out = truncate_section(text, max_chars=100)
    assert "truncated" in out


def test_invalid_quotes_downgraded():
    result = ComplianceLLMResult(
        status=ComplianceStatus.NON_COMPLIANT,
        severity=Severity.CRITICAL,
        contract_quote="not in text",
        policy_quote="also missing",
        rationale="Mismatch on liability cap requirements in the agreement.",
        confidence=0.9,
    )
    normalized = validate_and_normalize_quotes(
        result,
        contract_text="Contract limits liability to fees paid.",
        policy_text="Policy limits liability to twelve months fees.",
        preserve_non_compliant_on_quote_fail=False,
    )
    assert normalized.status == ComplianceStatus.INCONCLUSIVE


def test_nc_preserved_when_quote_fail_and_preserve_flag():
    result = ComplianceLLMResult(
        status=ComplianceStatus.NON_COMPLIANT,
        severity=Severity.CRITICAL,
        contract_quote="not in text",
        policy_quote="also missing",
        rationale="Mismatch on liability cap requirements in the agreement.",
        confidence=0.9,
    )
    normalized = validate_and_normalize_quotes(
        result,
        contract_text="Contract limits liability to fees paid.",
        policy_text="Policy limits liability to twelve months fees.",
        preserve_non_compliant_on_quote_fail=True,
    )
    assert normalized.status == ComplianceStatus.NON_COMPLIANT
    assert "status preserved" in normalized.rationale


def test_anchor_paraphrased_quote_finds_verbatim_span():
    haystack = (
        "The Supplier's liability shall not exceed the fees paid "
        "in the preceding twelve (12) months of service."
    )
    candidate = (
        "liability shall not exceed the fees paid in the preceding "
        "twelve (12) months of service"
    )
    anchored = anchor_quote_in_haystack(candidate, haystack)
    assert anchored
    assert anchored in haystack


def test_validate_nc_kept_after_anchor():
    contract_text = (
        "The Supplier's liability shall not exceed the fees paid "
        "in the preceding three (3) months of service."
    )
    policy_text = "Liability cap must be no less than twelve (12) months of fees."
    result = ComplianceLLMResult(
        status=ComplianceStatus.NON_COMPLIANT,
        severity=Severity.CRITICAL,
        contract_quote=(
            "liability shall not exceed the fees paid in the preceding "
            "three (3) months of service"
        ),
        policy_quote="Liability cap must be no less than twelve (12) months of fees.",
        rationale="Contract cap is below policy minimum.",
        confidence=0.9,
    )
    normalized = validate_and_normalize_quotes(
        result,
        contract_text=contract_text,
        policy_text=policy_text,
    )
    assert normalized.status == ComplianceStatus.NON_COMPLIANT
    assert normalized.contract_quote in contract_text


def test_bullet_quote_matches_contract_text() -> None:
    contract_text = "Support and respect internationally proclaimed human rights"
    assert quote_is_substring(
        "• Support and respect internationally proclaimed human rights",
        contract_text,
    )


def test_compliant_empty_policy_quote_allowed_when_aligned() -> None:
    contract_text = "Is or becomes publicly available through no act or omission"
    result = ComplianceLLMResult(
        status=ComplianceStatus.COMPLIANT,
        severity=Severity.INFO,
        contract_quote="Is or becomes publicly available through no act or omission",
        policy_quote="",
        rationale="Contract aligns with the policy exclusion for public information.",
        confidence=0.9,
    )
    normalized = validate_and_normalize_quotes(
        result,
        contract_text=contract_text,
        policy_text="Sensitive Data means protected information.",
    )
    assert normalized.status == ComplianceStatus.COMPLIANT
    assert allows_empty_policy_quote(
        ComplianceStatus.COMPLIANT,
        normalized.rationale,
        contract_ok=True,
    )


def test_compliant_incorporation_keeps_status_when_policy_paraphrased() -> None:
    contract_text = (
        "The Supplier shall comply with Xecurify's Security Practices Policy "
        "as updated from time to time."
    )
    policy_text = "All vendors must follow the Security Practices Policy requirements."
    result = ComplianceLLMResult(
        status=ComplianceStatus.COMPLIANT,
        severity=Severity.INFO,
        contract_quote=(
            "The Supplier shall comply with Xecurify's Security Practices Policy "
            "as updated from time to time."
        ),
        policy_quote="vendors must follow Security Practices Policy",
        rationale="Contract explicitly incorporates Xecurify's Security Practices Policy.",
        confidence=0.9,
    )
    normalized = validate_and_normalize_quotes(
        result,
        contract_text=contract_text,
        policy_text=policy_text,
        anchor_enabled=False,
    )
    assert normalized.status == ComplianceStatus.COMPLIANT
    assert normalized.policy_quote == ""


def test_nc_still_downgraded_when_policy_quote_invalid() -> None:
    contract_text = "Liability is capped at three months fees."
    policy_text = "Liability cap must be at least twelve months of fees."
    result = ComplianceLLMResult(
        status=ComplianceStatus.NON_COMPLIANT,
        severity=Severity.CRITICAL,
        contract_quote="Liability is capped at three months fees.",
        policy_quote="twelve months minimum cap",
        rationale="Contract cap is below policy minimum.",
        confidence=0.9,
    )
    normalized = validate_and_normalize_quotes(
        result,
        contract_text=contract_text,
        policy_text=policy_text,
        preserve_non_compliant_on_quote_fail=False,
    )
    assert normalized.status == ComplianceStatus.INCONCLUSIVE
