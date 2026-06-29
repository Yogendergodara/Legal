"""Evidence sufficiency gating for obligation compare (Phase R5 / PR-01)."""

from __future__ import annotations

import re

from document_core.schemas.chunk import RetrievalHit
from review_agent.config import ReviewSettings, get_settings
from review_agent.schemas.evidence_sufficiency import EvidenceSufficiencyResult
from review_agent.schemas.obligation import ContractObligation
from review_agent.schemas.obligation_retrieval import ObligationRetrievalBundle
from review_agent.schemas.routing_plan import CatalogMatchResult, ObligationRoutingPlan

_TOKEN_RE = re.compile(r"[a-z0-9]{3,}")


def _token_set(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def concept_overlap_score(
    *,
    plan: ObligationRoutingPlan,
    obligation: ContractObligation,
    hits: list[RetrievalHit],
) -> float:
    left = _token_set(" ".join(plan.concepts))
    left |= _token_set(obligation.text or "")
    if not left or not hits:
        return 0.0
    best = 0.0
    for hit in hits:
        parent = hit.parent_chunk
        right = _token_set(f"{parent.title} {parent.text}")
        union = left | right
        if not union:
            continue
        best = max(best, len(left & right) / len(union))
    return round(best, 3)


def candidate_doc_coverage(
    hits: list[RetrievalHit],
    candidate_doc_ids: list[str],
) -> float:
    if not candidate_doc_ids:
        return 0.0
    covered = {
        str(hit.parent_chunk.document_id)
        for hit in hits
        if str(hit.parent_chunk.document_id) in candidate_doc_ids
    }
    return round(len(covered) / len(candidate_doc_ids), 3)


def _max_hit_score(hits: list[RetrievalHit]) -> float:
    if not hits:
        return 0.0
    return max(hit.score for hit in hits)


def _lexical_overlap_passes(concept_overlap: float, settings: ReviewSettings) -> bool:
    if settings.evidence_min_concept_overlap <= 0:
        return True
    return concept_overlap >= settings.evidence_min_concept_overlap


def _rerank_bypass_passes(
    *,
    max_score: float,
    concept_overlap: float,
    routing_confidence: float,
    settings: ReviewSettings,
) -> bool:
    """PR-01 — cross-encoder score + marginal lexical overlap defers veto to compare LLM."""
    if not settings.evidence_rerank_bypass_enabled:
        return False
    if max_score < settings.evidence_min_score:
        return False
    if routing_confidence < settings.evidence_rerank_bypass_min_confidence:
        return False
    half_floor = settings.evidence_min_concept_overlap * 0.5
    if settings.evidence_min_concept_overlap <= 0:
        return True
    return concept_overlap >= half_floor


def _hits_pass_gates(
    *,
    hit_count: int,
    max_score: float,
    concept_overlap: float,
    doc_coverage: float,
    routing_confidence: float,
    settings: ReviewSettings,
) -> bool:
    if hit_count < settings.evidence_min_hits:
        return False
    if max_score < settings.evidence_min_score:
        return False
    if settings.evidence_min_doc_coverage > 0 and doc_coverage < settings.evidence_min_doc_coverage:
        return False
    if _lexical_overlap_passes(concept_overlap, settings):
        return True
    return _rerank_bypass_passes(
        max_score=max_score,
        concept_overlap=concept_overlap,
        routing_confidence=routing_confidence,
        settings=settings,
    )


def _candidate_doc_ids(
    match: CatalogMatchResult,
    bundle: ObligationRetrievalBundle,
) -> list[str]:
    return list(bundle.candidate_doc_ids or match.candidate_doc_ids or [])


def evaluate_evidence_sufficiency(
    *,
    obligation: ContractObligation,
    plan: ObligationRoutingPlan,
    match: CatalogMatchResult,
    bundle: ObligationRetrievalBundle,
    settings: ReviewSettings | None = None,
    expand_round: int = 0,
) -> EvidenceSufficiencyResult:
    cfg = settings or get_settings()
    hits = list(bundle.policy_hits)
    hit_count = len(hits)
    max_score = _max_hit_score(hits)
    overlap = concept_overlap_score(plan=plan, obligation=obligation, hits=hits)
    coverage = candidate_doc_coverage(hits, _candidate_doc_ids(match, bundle))

    base = EvidenceSufficiencyResult(
        obligation_id=obligation.obligation_id,
        hit_count=hit_count,
        max_relevance_score=round(max_score, 3),
        concept_overlap_score=overlap,
        candidate_doc_coverage=coverage,
        routing_confidence=plan.confidence,
        expand_round=expand_round,
        final_hits=hits,
    )

    if bundle.skipped_reason:
        reason = bundle.skipped_reason
        if reason == "ipc_preflight":
            reason = "routing_or_skip"
        return base.model_copy(update={"decision": "ipc", "reason": reason})

    if cfg.evidence_compare_on_catalog_candidates:
        if match.route_decision == "ipc" and not match.candidate_doc_ids:
            return base.model_copy(
                update={"decision": "ipc", "reason": "routing_or_skip"},
            )
    elif match.route_decision == "ipc":
        return base.model_copy(
            update={"decision": "ipc", "reason": "routing_or_skip"},
        )

    gates_pass = _hits_pass_gates(
        hit_count=hit_count,
        max_score=max_score,
        concept_overlap=overlap,
        doc_coverage=coverage,
        routing_confidence=plan.confidence,
        settings=cfg,
    )

    if plan.confidence < cfg.routing_ipc_max_confidence:
        candidates = _candidate_doc_ids(match, bundle)
        if gates_pass:
            pass
        elif (
            candidates
            and match.route_decision in ("expand", "compare")
            and expand_round < cfg.evidence_expand_max_rounds
        ):
            return base.model_copy(
                update={"decision": "expand", "reason": "insufficient_evidence"},
            )
        else:
            return base.model_copy(
                update={"decision": "ipc", "reason": "low_routing_confidence"},
            )

    if gates_pass:
        return base.model_copy(update={"decision": "compare", "reason": "evidence_sufficient"})

    if match.route_decision == "expand" and expand_round < cfg.evidence_expand_max_rounds:
        return base.model_copy(
            update={"decision": "expand", "reason": "insufficient_evidence"},
        )

    reason = "insufficient_hits"
    if hit_count and max_score < cfg.evidence_min_score:
        reason = "low_relevance_score"
    elif hit_count and not _lexical_overlap_passes(overlap, cfg):
        if _rerank_bypass_passes(
            max_score=max_score,
            concept_overlap=overlap,
            routing_confidence=plan.confidence,
            settings=cfg,
        ):
            return base.model_copy(
                update={"decision": "compare", "reason": "evidence_sufficient"},
            )
        reason = "low_concept_overlap"
    return base.model_copy(update={"decision": "ipc", "reason": reason})


def tally_skip_reasons(
    results: dict[str, EvidenceSufficiencyResult],
) -> dict[str, int]:
    """Count evidence decisions by reason for pipeline diagnostics (P0)."""
    counts: dict[str, int] = {}
    for result in results.values():
        key = (result.reason or result.decision or "unknown").strip() or "unknown"
        counts[key] = counts.get(key, 0) + 1
    return counts
