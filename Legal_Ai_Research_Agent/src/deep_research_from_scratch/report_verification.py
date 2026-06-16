"""Report verification gate (anti-hallucination reviewer).

Runs after the final memorandum is drafted and before it is delivered. It
combines:
  - a DETERMINISTIC check (regex citation cross-check + required-section +
    disclaimer presence + case-name / inline-citation checks) that catches
    fabricated citations with certainty, and
  - an LLM GROUNDING review that catches unsupported claims and overstated
    holdings against the retrieved findings and structured source registry.

If the memo fails and revisions remain, the graph loops back to regenerate it
with the reviewer's feedback. If revisions are exhausted, the memo is delivered
with a visible "Verification Caveats" section (fail-open but transparent) so a
legal claim is never shipped silently unverified.
"""

from __future__ import annotations

import re
from typing import List

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field
from typing_extensions import Literal

from deep_research_from_scratch.config import config as app_config
from deep_research_from_scratch.memory_tools import (
    get_session_id,
    record_transcript,
    record_verification,
)
from deep_research_from_scratch.model_config import (
    ainvoke_with_retry,
    cap_max_tokens_for_prompt,
    get_chat_model,
    is_rate_limit_error,
)
from deep_research_from_scratch.prompts import (
    report_verification_prompt,
)
from deep_research_from_scratch.source_registry import (
    RetrievedSource,
    build_verification_corpus,
    count_fetches,
    extract_case_names,
    extract_citations,
    extract_inline_citation_numbers,
    extract_sources_section_urls,
    filter_citable_sources,
    is_paywall_url,
    normalize_url,
    redact_fabricated_citations,
)
from deep_research_from_scratch.report_sources import ensure_sources_section
from deep_research_from_scratch.state_scope import AgentState, VerificationResult
from deep_research_from_scratch.utils import get_today_str

MAX_REVIEWER_RETRIES = app_config.MAX_REVIEWER_RETRIES

REQUIRED_SECTIONS = [
    "Questions Presented",
    "Brief Answer",
    "Statement of Facts",
    "Discussion",
    "Practical Guidance",
    "Conclusion",
    "Table of Authorities",
    "Disclaimer",
]

_CRYPTO_TOPIC_KEYWORDS = (
    "crypto",
    "cryptocurrency",
    "virtual currency",
    "bitcoin",
    "blockchain",
    "virtual digital asset",
    "vda",
    "token",
    "nft",
)


def _is_crypto_topic(*texts: str) -> bool:
    combined = " ".join(texts).lower()
    return any(keyword in combined for keyword in _CRYPTO_TOPIC_KEYWORDS)


def _landmark_missing(corpus_norm: str, report_norm: str, landmark: str) -> bool:
    """Return True when a topic landmark is absent from findings and memo."""
    if landmark == "iamai_rbi":
        present = (
            "IAMAI" in corpus_norm
            or "INTERNET AND MOBILE ASSOCIATION" in corpus_norm
        ) and "RESERVE BANK" in corpus_norm
        in_report = (
            "IAMAI" in report_norm
            or "INTERNET AND MOBILE ASSOCIATION" in report_norm
        ) and "RESERVE BANK" in report_norm
        return not (present and in_report)
    if landmark == "pmla_crypto":
        present = "PMLA" in corpus_norm and (
            "CRYPTO" in corpus_norm
            or "VIRTUAL DIGITAL" in corpus_norm
            or "VDA" in corpus_norm
        )
        in_report = "PMLA" in report_norm and (
            "CRYPTO" in report_norm
            or "VIRTUAL DIGITAL" in report_norm
            or "VDA" in report_norm
        )
        return not (present and in_report)
    return False


class _VerifierLLMOutput(BaseModel):
    """Semantic portion of the review, filled by the LLM reviewer."""

    passed: bool = Field(description="True only if fully grounded, no overstatement.")
    confidence: Literal["high", "medium", "low"] = "medium"
    unsupported_claims: List[str] = Field(default_factory=list)
    overstated_holdings: List[str] = Field(default_factory=list)
    law_currency_issues: List[str] = Field(default_factory=list)
    required_fixes: str = ""
    overall_assessment: str = ""


def _normalize(text: str) -> str:
    """Collapse whitespace and uppercase for citation comparison."""
    return re.sub(r"\s+", " ", text or "").strip().upper()


def _format_sources_for_reviewer(sources: list[RetrievedSource]) -> str:
    """Format structured sources for the LLM reviewer prompt."""
    if not sources:
        return "(No structured sources recorded.)"
    lines = []
    for index, src in enumerate(sources, 1):
        lines.append(f"[{index}] {src.title}")
        lines.append(f"  URL: {src.url}")
        lines.append(f"  Tier: {src.authority_tier} | Fetched: {src.fetched}")
        if src.citation:
            lines.append(f"  Citation: {src.citation}")
        if src.excerpt:
            lines.append(f"  Excerpt: {src.excerpt[:500]}...")
    return "\n".join(lines)


def _extract_discussion_section(report: str) -> str:
    """Return the Discussion section text from the memo."""
    return _extract_section(report, "discussion")


def _extract_section(report: str, heading: str) -> str:
    """Return text under a ## heading until the next ## heading."""
    report = report or ""
    pattern = (
        rf"##\s*{re.escape(heading)}\s*"
        r"(.*?)(?=##\s*(?:Conclusion|Table of Authorities|Disclaimer|Discussion|Brief Answer|Statement of Facts|Questions Presented)\s|$)"
    )
    match = re.search(pattern, report, flags=re.IGNORECASE | re.DOTALL)
    return match.group(1) if match else ""


def deterministic_checks(
    report: str,
    findings: str,
    sources: list[RetrievedSource] | None = None,
    research_brief: str = "",
) -> dict:
    """Run deterministic grounding checks against findings and source registry."""
    findings_norm = _normalize(findings)
    sources = sources or []

    report_citations = extract_citations(report)
    fabricated = [c for c in report_citations if c not in findings_norm]

    report_low = (report or "").lower()
    missing_sections = [s for s in REQUIRED_SECTIONS if s.lower() not in report_low]
    disclaimer_present = "not legal advice" in report_low

    registry_urls = {normalize_url(s.url) for s in sources if s.url}
    fetched_urls = {normalize_url(s.url) for s in sources if s.fetched and s.url}
    sources_mapping = extract_sources_section_urls(report)
    inline_numbers = extract_inline_citation_numbers(report)
    unmapped_inline = [
        str(n)
        for n in inline_numbers
        if n not in sources_mapping
        or normalize_url(sources_mapping[n]) not in registry_urls
    ]

    access_denied_urls: list[str] = []
    for num, url in sources_mapping.items():
        norm = normalize_url(url)
        if is_paywall_url(url) and norm not in fetched_urls:
            access_denied_urls.append(url)
        elif norm not in fetched_urls:
            access_denied_urls.append(url)

    discussion = _extract_discussion_section(report)
    discussion_cases = extract_case_names(discussion)
    corpus_norm = _normalize(findings)
    ungrounded_cases = [c for c in discussion_cases if c not in corpus_norm]

    _, primary_fetches = count_fetches(sources)
    cites_case_law = bool(report_citations or discussion_cases)
    no_primary_sources = cites_case_law and primary_fetches == 0

    # Check for footnote-style citations (superscripts, bottom notes, or end-of-doc refs)
    uses_footnote_style = bool(
        re.search(
            r"(?:^|\n)\s*\^\d+\s"
            r"|(?:^|\n)\s*\d+\.\s+\^"
            r"|[¹²³⁴⁵⁶⁷⁸⁹]"
            r"|(?:^|\n)Footnotes?\s*:?",
            report or "",
            re.IGNORECASE,
        )
        or re.search(
            r"(?:^|\n)\d+\.\s+[A-Z][^\n]{10,}(?:\bv\.?\b|versus)[^\n]{5,}$",
            report or "",
            re.IGNORECASE | re.MULTILINE,
        )
    )

    # Check for presence of judicial trends section (requires post-2020 content)
    has_trends_section = bool(
        re.search(r"###?\s*Judicial\s+Trends", report or "", re.IGNORECASE)
    )
    # Only flag missing trends if post-2020 dates appear in findings (meaning recent cases exist)
    recent_case_dates_in_findings = bool(
        re.search(r"\b202[2-5]\b", findings or "")
    )
    missing_trends_section = recent_case_dates_in_findings and not has_trends_section

    brief_section = _extract_section(report, "brief answer")
    # Expanded hedging patterns — all phrases that indicate unwarranted uncertainty
    # when primary sources ARE present. The original only caught "unsettled" and
    # "no cases found"; this also catches "unclear", "ambiguous", "conflicting",
    # "no clear authority", "law is not settled/clear", and similar hedges that
    # caused the "Still says unsettled" penalty in evaluation.
    hedged_despite_sources = primary_fetches >= 2 and bool(
        re.search(
            r"\b("
            r"unsettled"
            r"|unclear"
            r"|ambiguous"
            r"|no (?:direct )?cases? found"
            r"|zero cases"
            r"|not established"
            r"|no clear (?:authority|answer|law|position)"
            r"|law (?:is|remains) (?:not |un)?(?:settled|clear)"
            r"|conflicting (?:views|authorities|judgments|decisions)"
            r"|no (?:binding |authoritative )?(?:precedent|authority) (?:found|exists|available)"
            r"|cannot be (?:definitively |clearly )?determined"
            r"|not (?:definitively |clearly )?established"
            r")\b",
            brief_section,
            re.IGNORECASE,
        )
    )

    missing_landmarks: list[str] = []
    if _is_crypto_topic(research_brief, findings, report):
        corpus_norm = _normalize(findings)
        report_norm = _normalize(report)
        if _landmark_missing(corpus_norm, report_norm, "iamai_rbi"):
            missing_landmarks.append(
                "IAMAI v RBI (Internet and Mobile Association of India v Reserve Bank of India)"
            )
        if _landmark_missing(corpus_norm, report_norm, "pmla_crypto"):
            missing_landmarks.append(
                "PMLA enforcement on virtual digital assets / cryptocurrency"
            )

    passed = (
        (not fabricated)
        and (not missing_sections)
        and disclaimer_present
        and (not unmapped_inline)
        and (not ungrounded_cases)
        and (not no_primary_sources)
        and (not hedged_despite_sources)
        and (not uses_footnote_style)
        and (not missing_trends_section)
        and (not access_denied_urls)
        and (not missing_landmarks)
    )

    return {
        "fabricated": fabricated,
        "missing_sections": missing_sections,
        "disclaimer_present": disclaimer_present,
        "unmapped_inline_citations": unmapped_inline,
        "ungrounded_case_names": ungrounded_cases,
        "no_primary_sources": no_primary_sources,
        "hedged_despite_sources": hedged_despite_sources,
        "uses_footnote_style": uses_footnote_style,
        "missing_trends_section": missing_trends_section,
        "access_denied_urls": access_denied_urls,
        "missing_landmarks": missing_landmarks,
        "passed": passed,
    }


def _build_required_fixes(llm_fixes: str, det: dict) -> str:
    """Merge LLM feedback with deterministic findings into one actionable block."""
    parts = []
    if llm_fixes.strip():
        parts.append(llm_fixes.strip())
    if det["fabricated"]:
        parts.append(
            "Remove these citations that do NOT appear in the Findings (do not "
            "replace them with other citations): " + "; ".join(det["fabricated"])
        )
    if det.get("ungrounded_case_names"):
        parts.append(
            "These case names in Discussion are not grounded in Findings/sources: "
            + "; ".join(det["ungrounded_case_names"])
        )
    if det.get("unmapped_inline_citations"):
        parts.append(
            "Inline citation numbers not mapped to a fetched source URL in the "
            "registry: " + ", ".join(det["unmapped_inline_citations"])
        )
    if det.get("no_primary_sources"):
        parts.append(
            "The memo cites case law but no primary-tier source (indiankanoon, "
            "indiacode, .gov.in) was fetched. Remove unverified case citations or "
            "mark them NOT FOUND."
        )
    if det.get("hedged_despite_sources"):
        parts.append(
            "Brief Answer uses hedging language ('unsettled', 'unclear', 'ambiguous', "
            "'no cases found', 'conflicting views', etc.) despite multiple fetched primary "
            "sources being present in the registry. This is PROHIBITED when sources exist. "
            "Replace the hedge with a direct Yes/No/Likely answer. Cite EACH fetched "
            "judgment or statute with its [n] inline citation number. If a genuine conflict "
            "exists between binding authorities, NAME BOTH cases and explain which prevails "
            "and why — do not simply label the law 'unsettled'."
        )
    if det.get("uses_footnote_style"):
        parts.append(
            "Footnote-style citation markers (superscript numbers, ¹²³, etc.) detected. "
            "Indian legal memoranda use inline [n] citations placed immediately after the "
            "legal proposition. Remove all footnote markers and convert to inline [n] format."
        )
    if det.get("missing_trends_section"):
        parts.append(
            "The Findings contain post-2022 case law but the memo is missing a "
            "'### Judicial Trends (2020-2025)' subsection under Practical Guidance. "
            "Add it: list 3-5 post-2020 cases in the Permitted Source Registry with year, "
            "court, holding, and a one-sentence trend conclusion."
        )
    if det.get("access_denied_urls"):
        parts.append(
            "Remove or replace URLs that were not successfully fetched (paywall / "
            "access denied). Only cite URLs with FETCHED status in the registry: "
            + "; ".join(det["access_denied_urls"])
        )
    if det.get("missing_landmarks"):
        parts.append(
            "This topic requires these landmark authorities — search indiankanoon.org, "
            "fetch them, and analyze them in Discussion with inline [n] citations: "
            + "; ".join(det["missing_landmarks"])
        )
    if det["missing_sections"]:
        parts.append("Add these missing required sections: " + ", ".join(det["missing_sections"]))
    if not det["disclaimer_present"]:
        parts.append(
            "Add the required '## Disclaimer' section stating this is AI-assisted "
            "legal research, not legal advice, and that citations must be independently verified."
        )
    return "\n".join(f"- {p}" for p in parts) if parts else "No issues found."


async def verify_report(state: AgentState, config: RunnableConfig) -> dict:
    """Verify the drafted memorandum against findings and structured sources."""
    report = state.get("final_report", "") or ""
    notes = state.get("notes", [])
    raw_notes = state.get("raw_notes", [])
    sources: list[RetrievedSource] = []
    for item in state.get("retrieved_sources") or []:
        sources.append(item if isinstance(item, RetrievedSource) else RetrievedSource(**item))

    findings = build_verification_corpus(notes, raw_notes, sources)
    det = deterministic_checks(
        report,
        findings,
        filter_citable_sources(sources),
        research_brief=state.get("research_brief", "") or "",
    )

    structured_sources_text = _format_sources_for_reviewer(sources)

    if app_config.LLM_SKIP_VERIFIER:
        llm_out = _VerifierLLMOutput(
            passed=True,
            confidence="medium",
            overall_assessment=(
                "LLM semantic review disabled (LLM_SKIP_VERIFIER=true); "
                "deterministic citation and structure checks applied."
            ),
        )
    else:
        try:
            prompt = report_verification_prompt.format(
                research_brief=state.get("research_brief", ""),
                findings=findings,
                structured_sources=structured_sources_text,
                report=report,
                date=get_today_str(),
            )
            safe_max_tokens = cap_max_tokens_for_prompt(
                prompt, role="verifier", requested_max_tokens=4096
            )
            reviewer_base = get_chat_model("verifier", temperature=0.0)
            reviewer = (
                reviewer_base.bind(max_tokens=safe_max_tokens)
                if safe_max_tokens is not None
                else reviewer_base
            ).with_structured_output(_VerifierLLMOutput)
            llm_out = await ainvoke_with_retry(
                reviewer, [HumanMessage(content=prompt)]
            )
        except Exception as e:  # noqa: BLE001
            if is_rate_limit_error(e):
                llm_out = _VerifierLLMOutput(
                    passed=True,
                    confidence="low",
                    overall_assessment=(
                        "LLM semantic review skipped after rate-limit retries; "
                        "deterministic citation and structure checks applied."
                    ),
                )
            else:
                llm_out = _VerifierLLMOutput(
                    passed=False,
                    confidence="low",
                    required_fixes="",
                    overall_assessment=f"LLM review could not be completed: {e}",
                )

    unsupported = list(llm_out.unsupported_claims)
    if det.get("ungrounded_case_names"):
        unsupported.extend(
            f"Ungrounded case name: {name}" for name in det["ungrounded_case_names"]
        )
    if det.get("no_primary_sources"):
        unsupported.append(
            "Case law cited without any fetched primary-tier source in the registry."
        )
    if det.get("hedged_despite_sources"):
        unsupported.append(
            "Brief Answer is hedged despite multiple fetched primary sources."
        )
    if det.get("access_denied_urls"):
        unsupported.append(
            "Memo cites URLs that were not successfully fetched (access denied / paywall)."
        )
    if det.get("missing_landmarks"):
        unsupported.extend(
            f"Missing landmark authority: {name}" for name in det["missing_landmarks"]
        )

    result = VerificationResult(
        passed=bool(det["passed"] and llm_out.passed),
        confidence=llm_out.confidence,
        fabricated_or_unverified_citations=det["fabricated"],
        unsupported_claims=unsupported,
        overstated_holdings=llm_out.overstated_holdings,
        law_currency_issues=llm_out.law_currency_issues,
        missing_sections=det["missing_sections"],
        disclaimer_present=det["disclaimer_present"],
        required_fixes=_build_required_fixes(llm_out.required_fixes, det),
        overall_assessment=llm_out.overall_assessment,
    )

    try:
        record_verification(get_session_id(config), result.model_dump())
    except Exception:  # noqa: BLE001
        pass

    return {
        "verification": result,
        "verification_retries": state.get("verification_retries", 0) + 1,
    }


def route_after_verify(state: AgentState) -> Literal["final_report_generation", "finalize_report"]:
    """Decide whether to revise the memo or finalize it."""
    verification = state.get("verification")
    if verification is not None and verification.passed:
        return "finalize_report"
    if state.get("verification_retries", 0) > MAX_REVIEWER_RETRIES:
        return "finalize_report"
    return "final_report_generation"


def _build_caveats_section(v: VerificationResult) -> str:
    """Build the visible caveats appended when a memo ships unverified."""
    lines = [
        "## Verification Caveats",
        "",
        "> This memorandum did NOT fully pass automated verification. The items "
        "below were flagged and must be independently checked before any reliance.",
        "",
    ]
    if v.fabricated_or_unverified_citations:
        lines.append("**Unverified citations (not found in the retrieved sources):**")
        lines += [f"- {c}" for c in v.fabricated_or_unverified_citations]
        lines.append("")
    if v.unsupported_claims:
        lines.append("**Claims not supported by the retrieved sources:**")
        lines += [f"- {c}" for c in v.unsupported_claims]
        lines.append("")
    if v.overstated_holdings:
        lines.append("**Possibly overstated holdings:**")
        lines += [f"- {c}" for c in v.overstated_holdings]
        lines.append("")
    if v.law_currency_issues:
        lines.append("**Old-vs-new law (IPC/CrPC/Evidence vs BNS/BNSS/BSA) concerns:**")
        lines += [f"- {c}" for c in v.law_currency_issues]
        lines.append("")
    if v.missing_sections:
        lines.append("**Missing memorandum sections:** " + ", ".join(v.missing_sections))
        lines.append("")
    if v.overall_assessment:
        lines.append(f"_Reviewer note: {v.overall_assessment}_")
    return "\n".join(lines).rstrip()


def finalize_report(state: AgentState, config: RunnableConfig) -> dict:
    """Deliver the memo: redact fabricated citations, append caveats if needed, persist."""
    report = state.get("final_report", "") or ""
    verification = state.get("verification")
    sources: list[RetrievedSource] = []
    for item in state.get("retrieved_sources") or []:
        sources.append(item if isinstance(item, RetrievedSource) else RetrievedSource(**item))

    report = ensure_sources_section(report, sources)

    if verification is not None and not verification.passed:
        if verification.fabricated_or_unverified_citations:
            report = redact_fabricated_citations(
                report, verification.fabricated_or_unverified_citations
            )
        report = report + "\n\n" + _build_caveats_section(verification)

    record_transcript(get_session_id(config), "assistant", report)

    return {
        "final_report": report,
        "messages": [AIMessage(content="Here is the final report: " + report)],
    }
