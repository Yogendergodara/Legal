"""Lexical pre-screen before batched LLM compliance (conservative gate)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from document_core.schemas.chunk import RetrievalHit
from document_core.schemas.compliance import ComplianceFinding, ComplianceStatus, Severity
from document_core.search.lexical import score_query

from review_agent.config import ReviewSettings, get_settings
from review_agent.schemas.alignment import AlignmentRecord
from review_agent.schemas.review_category import ReviewCategory
from review_agent.services.compliance import _short_quote, compare_sections


@dataclass
class PrescreenOutcome:
    resolved: list[ComplianceFinding]
    deferred: list[ReviewCategory]


def prescreen_category(
    category: ReviewCategory,
    policy_hits: list[RetrievalHit],
    contract_hits: list[RetrievalHit],
    alignment: AlignmentRecord,
    retrieval_meta: dict,
    *,
    settings: ReviewSettings | None = None,
) -> tuple[ComplianceFinding | None, ReviewCategory | None]:
    """Return (finding, None) if resolved without LLM, else (None, category) to defer."""
    cfg = settings or get_settings()
    base_meta = {
        **retrieval_meta,
        "compliance_mode": "hybrid",
        "compliance_pass": "prescreen",
        "combined_score": alignment.combined_score,
    }

    if not policy_hits:
        return (
            ComplianceFinding(
                finding_id=str(uuid.uuid4()),
                dimension_id=category.category_id,
                dimension_label=category.label,
                status=ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
                severity=Severity.INFO,
                rationale="No matching policy section retrieved for this dimension.",
                metadata={**base_meta, "prescreen_decision": "no_policy"},
            ),
            None,
        )

    if not contract_hits:
        return None, category

    if not cfg.compliance_prescreen_enabled:
        return None, category

    if alignment.combined_score < cfg.compliance_retrieval_score_min:
        return None, category

    policy = policy_hits[0].parent_chunk
    contract = contract_hits[0].parent_chunk
    overlap = score_query(policy.text, contract.text)
    policy_self = score_query(policy.text, policy.text)
    if policy_self <= 0:
        return None, category

    ratio = overlap / policy_self

    if overlap < cfg.compliance_prescreen_noncompliant_max:
        return (
            ComplianceFinding(
                finding_id=str(uuid.uuid4()),
                dimension_id=category.category_id,
                dimension_label=category.label,
                status=ComplianceStatus.NON_COMPLIANT,
                severity=Severity.IMPORTANT,
                contract_quote=_short_quote(contract.text),
                policy_quote=_short_quote(policy.text),
                contract_section_id=contract.section_id,
                policy_section_id=policy.section_id,
                policy_document_id=policy.document_id,
                rationale=(
                    "Contract section weakly aligns with policy language for this dimension "
                    f"(lexical overlap={overlap:.2f})."
                ),
                metadata={
                    **base_meta,
                    "prescreen_decision": "non_compliant",
                    "overlap_score": overlap,
                    "overlap_ratio": ratio,
                },
            ),
            None,
        )

    if ratio >= cfg.compliance_prescreen_compliant_min:
        return (
            ComplianceFinding(
                finding_id=str(uuid.uuid4()),
                dimension_id=category.category_id,
                dimension_label=category.label,
                status=ComplianceStatus.COMPLIANT,
                severity=Severity.INFO,
                contract_quote=_short_quote(contract.text),
                policy_quote=_short_quote(policy.text),
                contract_section_id=contract.section_id,
                policy_section_id=policy.section_id,
                policy_document_id=policy.document_id,
                rationale=(
                    "Contract section appears to cover policy requirements for this dimension "
                    f"(lexical overlap={overlap:.2f})."
                ),
                metadata={
                    **base_meta,
                    "prescreen_decision": "compliant",
                    "overlap_score": overlap,
                    "overlap_ratio": ratio,
                },
            ),
            None,
        )

    return None, category


def run_prescreen(
    categories: list[ReviewCategory],
    policy_hits_by_category: dict[str, list[RetrievalHit]],
    contract_hits_by_category: dict[str, list[RetrievalHit]],
    alignment_by_category: dict[str, AlignmentRecord],
    retrieval_meta_by_category: dict[str, dict],
    *,
    settings: ReviewSettings | None = None,
) -> PrescreenOutcome:
    """Split categories into prescreen-resolved findings vs deferred to LLM."""
    cfg = settings or get_settings()
    resolved: list[ComplianceFinding] = []
    deferred: list[ReviewCategory] = []

    for category in categories:
        alignment = alignment_by_category.get(category.category_id)
        if alignment is None:
            deferred.append(category)
            continue

        finding, defer_cat = prescreen_category(
            category,
            policy_hits_by_category.get(category.category_id, []),
            contract_hits_by_category.get(category.category_id, []),
            alignment,
            retrieval_meta_by_category.get(category.category_id, {}),
            settings=cfg,
        )
        if finding is not None:
            resolved.append(finding)
        elif defer_cat is not None:
            deferred.append(defer_cat)
        else:
            deferred.append(category)

    if not cfg.compliance_prescreen_enabled:
        return PrescreenOutcome(resolved=[], deferred=list(categories))

    return PrescreenOutcome(resolved=resolved, deferred=deferred)


def lexical_finding_for_category(
    category: ReviewCategory,
    policy_hits: list[RetrievalHit],
    contract_hits: list[RetrievalHit],
    retrieval_meta: dict,
) -> ComplianceFinding | None:
    """Fallback lexical compare (same as legacy lexical mode)."""
    finding = compare_sections(
        dimension_id=category.category_id,
        dimension_label=category.label,
        contract_hits=contract_hits,
        policy_hits=policy_hits,
    )
    if finding is None:
        return None
    return finding.model_copy(
        update={
            "metadata": {
                **finding.metadata,
                **retrieval_meta,
                "compliance_mode": "hybrid",
                "compliance_pass": "lexical",
            }
        }
    )
