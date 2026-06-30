"""Quote validation helpers for section-first LLM compare."""

from __future__ import annotations

import re

from document_core.schemas.compliance import ComplianceStatus, Severity
from document_core.services.quote_match import quote_matches

from review_agent.schemas.compliance_llm import ComplianceLLMResult

QUOTE_VALIDATE_DOWNGRADE_MARKER = "Downgraded: model quotes were not exact substrings"

_ALIGNMENT_RATIONALE = re.compile(
    r"(?i)\b("
    r"aligns? with|explicitly aligns|incorporat(?:es|ion)|no material deviation|"
    r"no deviation|satisfies|adopted by reference|consistent with|complies with|"
    r"matches the policy|explicitly supports|treated as compliant"
    r")\b"
)


def truncate_section(text: str, max_chars: int) -> str:
    """Truncate long sections without breaking mid-word when possible."""
    cleaned = text.strip()
    if len(cleaned) <= max_chars:
        return cleaned
    cut = cleaned[:max_chars]
    if "\n\n" in cut:
        cut = cut.rsplit("\n\n", 1)[0]
    elif " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut + "\n\n[truncated]"


def quote_is_substring(quote: str, haystack: str) -> bool:
    return quote_matches(quote, haystack)


def allows_compliant_without_policy_quote(
    status: ComplianceStatus,
    rationale: str,
    *,
    contract_ok: bool,
) -> bool:
    """COMPLIANT findings may omit policy_quote when contract grounds alignment."""
    return (
        status == ComplianceStatus.COMPLIANT
        and contract_ok
        and bool(_ALIGNMENT_RATIONALE.search(rationale or ""))
    )


def allows_empty_policy_quote(
    status: ComplianceStatus,
    rationale: str,
    *,
    contract_ok: bool,
) -> bool:
    return allows_compliant_without_policy_quote(
        status, rationale, contract_ok=contract_ok
    )


def anchor_quote_in_haystack(
    candidate: str,
    haystack: str,
    *,
    min_tokens: int = 8,
    overlap_threshold: float = 0.8,
) -> str:
    """Find a verbatim haystack span that best matches a paraphrased candidate quote."""
    cleaned = (candidate or "").strip()
    if not cleaned or not (haystack or "").strip():
        return ""
    if quote_is_substring(cleaned, haystack):
        return cleaned

    cand_tokens = cleaned.split()
    if len(cand_tokens) < 3:
        return ""

    hay_words = haystack.split()
    if len(hay_words) < len(cand_tokens):
        return ""

    cand_set = {t.lower() for t in cand_tokens}
    required_overlap = min(min_tokens, len(cand_tokens))
    best_span = ""
    best_ratio = 0.0

    for window_size in range(len(cand_tokens), min(len(cand_tokens) + 4, len(hay_words)) + 1):
        for start in range(len(hay_words) - window_size + 1):
            window = hay_words[start : start + window_size]
            window_set = {w.lower() for w in window}
            overlap = len(cand_set & window_set) / len(cand_set)
            if overlap > best_ratio:
                best_ratio = overlap
                best_span = " ".join(window)

    if best_ratio >= overlap_threshold and len(best_span.split()) >= min(3, required_overlap):
        if quote_is_substring(best_span, haystack):
            return best_span
    return ""


def validate_and_normalize_quotes(
    result: ComplianceLLMResult,
    *,
    contract_text: str,
    policy_text: str,
    quote_stats: dict[str, int] | None = None,
    anchor_enabled: bool = True,
    preserve_non_compliant_on_quote_fail: bool = False,
) -> ComplianceLLMResult:
    """Ensure quotes are verbatim substrings; anchor paraphrases before downgrade."""
    contract_ok = quote_is_substring(result.contract_quote, contract_text)
    if not contract_ok and result.contract_quote and anchor_enabled:
        anchored = anchor_quote_in_haystack(result.contract_quote, contract_text)
        if anchored:
            contract_ok = True
            result = result.model_copy(update={"contract_quote": anchored})
            if quote_stats is not None:
                quote_stats["compare_quote_anchored"] = quote_stats.get("compare_quote_anchored", 0) + 1

    if not (policy_text or "").strip():
        policy_ok = not (result.policy_quote or "").strip()
    else:
        policy_ok = quote_is_substring(result.policy_quote, policy_text)
        if not policy_ok and result.policy_quote and anchor_enabled:
            anchored = anchor_quote_in_haystack(result.policy_quote, policy_text)
            if anchored:
                policy_ok = True
                result = result.model_copy(update={"policy_quote": anchored})
                if quote_stats is not None:
                    quote_stats["compare_quote_anchored"] = quote_stats.get("compare_quote_anchored", 0) + 1

    if result.status in (ComplianceStatus.COMPLIANT, ComplianceStatus.NON_COMPLIANT):
        if allows_compliant_without_policy_quote(
            result.status,
            result.rationale,
            contract_ok=contract_ok,
        ):
            if not policy_ok:
                result = result.model_copy(update={"policy_quote": ""})
            policy_ok = True
        if not contract_ok or not policy_ok:
            if (
                preserve_non_compliant_on_quote_fail
                and result.status == ComplianceStatus.NON_COMPLIANT
                and contract_ok
            ):
                return result.model_copy(
                    update={
                        "contract_quote": result.contract_quote if contract_ok else "",
                        "policy_quote": result.policy_quote if policy_ok else "",
                        "rationale": (
                            f"{result.rationale} "
                            f"({QUOTE_VALIDATE_DOWNGRADE_MARKER}; status preserved.)"
                        )[:2000],
                    }
                )
            return ComplianceLLMResult(
                status=ComplianceStatus.INCONCLUSIVE,
                severity=Severity.IMPORTANT,
                contract_quote=result.contract_quote if contract_ok else "",
                policy_quote=result.policy_quote if policy_ok else "",
                rationale=(
                    f"{result.rationale} "
                    f"({QUOTE_VALIDATE_DOWNGRADE_MARKER} of the provided sections.)"
                )[:2000],
                confidence=result.confidence,
            )
    else:
        if result.contract_quote and not contract_ok:
            result = result.model_copy(update={"contract_quote": ""})
        if result.policy_quote and not policy_ok:
            result = result.model_copy(update={"policy_quote": ""})

    return result


def validate_gap_item_quotes(
    result: ComplianceLLMResult,
    *,
    contract_text: str,
    anchor_enabled: bool = True,
) -> ComplianceLLMResult:
    """Validate contract quotes for gap LLM output (no policy text)."""
    contract_ok = quote_is_substring(result.contract_quote, contract_text)
    if not contract_ok and result.contract_quote and anchor_enabled:
        anchored = anchor_quote_in_haystack(result.contract_quote, contract_text)
        if anchored:
            contract_ok = True
            result = result.model_copy(update={"contract_quote": anchored})

    if result.status == ComplianceStatus.NON_COMPLIANT and not contract_ok:
        return ComplianceLLMResult(
            status=ComplianceStatus.INCONCLUSIVE,
            severity=Severity.IMPORTANT,
            contract_quote=result.contract_quote if contract_ok else "",
            policy_quote="",
            rationale=(
                f"{result.rationale} "
                "(Downgraded: contract quote was not an exact substring.)"
            )[:2000],
            confidence=result.confidence,
        )
    if result.contract_quote and not contract_ok:
        result = result.model_copy(update={"contract_quote": ""})
    return result
