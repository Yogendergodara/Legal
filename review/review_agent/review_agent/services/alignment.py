"""Build policy–contract alignment records from retrieval hits."""

from __future__ import annotations

from document_core.schemas.chunk import RetrievalHit

from review_agent.config import ReviewSettings, get_settings
from review_agent.schemas.alignment import AlignmentRecord
from review_agent.schemas.review_category import ReviewCategory
from review_agent.services.compliance_llm import _truncate_section


def _hit_score(hits: list[RetrievalHit]) -> float:
    if not hits:
        return 0.0
    return float(hits[0].score)


def build_alignment_record(
    category: ReviewCategory,
    policy_hits: list[RetrievalHit],
    contract_hits: list[RetrievalHit],
    retrieval_meta: dict,
    *,
    settings: ReviewSettings | None = None,
) -> AlignmentRecord:
    """Map retrieval output to a truncated alignment record for batch prompts."""
    cfg = settings or get_settings()
    policy_score = _hit_score(policy_hits)
    contract_score = _hit_score(contract_hits)
    combined = (policy_score + contract_score) / 2.0 if (policy_hits or contract_hits) else 0.0

    policy_text = ""
    policy_doc_id = category.policy_document_id
    policy_section_id = category.policy_section_id
    if policy_hits:
        policy = policy_hits[0].parent_chunk
        policy_text = _truncate_section(policy.text, cfg.compliance_max_section_chars)
        policy_doc_id = policy.document_id
        policy_section_id = policy.section_id

    contract_text = ""
    if contract_hits:
        contract = contract_hits[0].parent_chunk
        contract_text = _truncate_section(contract.text, cfg.compliance_max_section_chars)

    return AlignmentRecord(
        category_id=category.category_id,
        policy_document_id=policy_doc_id,
        policy_section_id=policy_section_id,
        policy_hit_score=policy_score,
        contract_hit_score=contract_score,
        combined_score=combined,
        policy_text_excerpt=policy_text,
        contract_text_excerpt=contract_text,
        retrieval_method=str(retrieval_meta.get("retrieval_method", "")),
    )
