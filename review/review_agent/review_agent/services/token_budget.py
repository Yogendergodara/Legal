"""Rough token budgeting for section compare batches."""

from __future__ import annotations

import math

from document_core.schemas.chunk import IndexedChunk, RetrievalHit
from review_agent.config import ReviewSettings, get_settings
from review_agent.schemas.obligation import ContractObligation
from review_agent.services.compare_prompt_tokens import (
    estimate_compare_batch_tokens,
    estimate_obligation_batch_tokens,
    estimate_tokens,
)
from review_agent.services.playbook_context import PlaybookHints

__all__ = [
    "compare_batch_split_stats",
    "effective_compare_max_tokens",
    "estimate_obligation_batch_tokens",
    "estimate_section_batch_tokens",
    "estimate_tokens",
    "max_batch_estimated_tokens",
    "split_batch_by_token_budget",
    "split_obligations_by_token_budget",
]


def effective_compare_max_tokens(max_tokens: int, settings: ReviewSettings | None = None) -> int:
    """Posture-aware compare token cap (Phase D / Phase B)."""
    cfg = settings or get_settings()
    if not cfg.llm_review_posture_enabled:
        return max_tokens
    from review_agent.resilience.failure_policy import ReviewPosture, get_current_review_posture

    posture = get_current_review_posture()
    if posture == ReviewPosture.DEGRADED:
        return max(1, int(max_tokens * 0.75))
    if posture == ReviewPosture.HOT:
        return max(1, int(max_tokens * 0.85))
    return max_tokens


def compare_batch_split_stats(
    section_count: int,
    batches: list[list[IndexedChunk]],
    batch_size: int,
) -> dict[str, int]:
    """Observability: configured vs token-limited batch counts."""
    config_max = math.ceil(section_count / batch_size) if section_count and batch_size else 0
    actual = len(batches)
    token_limited = max(0, actual - config_max) if config_max else 0
    return {
        "llm_batches_config_max": config_max,
        "llm_batches_token_limited": token_limited,
    }


def estimate_section_batch_tokens(
    sections: list[IndexedChunk],
    bundles: dict[str, list[RetrievalHit]],
    *,
    settings: ReviewSettings | None = None,
    playbook_hints_by_document: dict[str, PlaybookHints] | None = None,
    categories_by_section: dict[str, list[str]] | None = None,
    extra_context_by_section: dict[str, str] | None = None,
) -> int:
    cfg = settings or get_settings()
    return estimate_compare_batch_tokens(
        sections,
        bundles,
        settings=cfg,
        playbook_hints_by_document=playbook_hints_by_document,
        categories_by_section=categories_by_section,
        extra_context_by_section=extra_context_by_section,
    )


def _split_first_fit(
    sections: list[IndexedChunk],
    *,
    batch_size: int,
    max_tokens: int,
    bundles: dict[str, list[RetrievalHit]],
    settings: ReviewSettings,
    playbook_hints_by_document: dict[str, PlaybookHints] | None,
    categories_by_section: dict[str, list[str]] | None,
    extra_context_by_section: dict[str, str] | None,
) -> list[list[IndexedChunk]]:
    batches: list[list[IndexedChunk]] = []
    current: list[IndexedChunk] = []
    for section in sections:
        trial = current + [section]
        if len(trial) > batch_size:
            if current:
                batches.append(current)
            current = [section]
        elif (
            estimate_section_batch_tokens(
                trial,
                bundles,
                settings=settings,
                playbook_hints_by_document=playbook_hints_by_document,
                categories_by_section=categories_by_section,
                extra_context_by_section=extra_context_by_section,
            )
            > max_tokens
            and current
        ):
            batches.append(current)
            current = [section]
        else:
            current = trial
    if current:
        batches.append(current)
    return batches


def _split_best_fit(
    sections: list[IndexedChunk],
    *,
    batch_size: int,
    max_tokens: int,
    bundles: dict[str, list[RetrievalHit]],
    settings: ReviewSettings,
    playbook_hints_by_document: dict[str, PlaybookHints] | None,
    categories_by_section: dict[str, list[str]] | None,
    extra_context_by_section: dict[str, str] | None,
) -> list[list[IndexedChunk]]:
    if not sections:
        return []

    def _batch_tokens(batch: list[IndexedChunk]) -> int:
        return estimate_section_batch_tokens(
            batch,
            bundles,
            settings=settings,
            playbook_hints_by_document=playbook_hints_by_document,
            categories_by_section=categories_by_section,
            extra_context_by_section=extra_context_by_section,
        )

    ranked = sorted(
        sections,
        key=lambda s: _batch_tokens([s]),
        reverse=True,
    )
    batches: list[list[IndexedChunk]] = []
    for section in ranked:
        best_idx: int | None = None
        best_slack: int | None = None
        for idx, batch in enumerate(batches):
            if len(batch) >= batch_size:
                continue
            tokens = _batch_tokens(batch + [section])
            if tokens <= max_tokens:
                slack = max_tokens - tokens
                if best_slack is None or slack < best_slack:
                    best_idx = idx
                    best_slack = slack
        if best_idx is not None:
            batches[best_idx].append(section)
        else:
            batches.append([section])
    return batches


def split_batch_by_token_budget(
    sections: list[IndexedChunk],
    *,
    batch_size: int,
    max_tokens: int,
    bundles: dict[str, list[RetrievalHit]],
    settings: ReviewSettings | None = None,
    playbook_hints_by_document: dict[str, PlaybookHints] | None = None,
    categories_by_section: dict[str, list[str]] | None = None,
    extra_context_by_section: dict[str, str] | None = None,
) -> list[list[IndexedChunk]]:
    """Group sections into batches respecting size and token budget."""
    if not sections:
        return []
    sections = sorted(sections, key=lambda s: s.section_id)
    cfg = settings or get_settings()
    kwargs = {
        "batch_size": batch_size,
        "max_tokens": max_tokens,
        "bundles": bundles,
        "settings": cfg,
        "playbook_hints_by_document": playbook_hints_by_document,
        "categories_by_section": categories_by_section,
        "extra_context_by_section": extra_context_by_section,
    }
    if cfg.compare_token_pack_mode == "best_fit":
        return _split_best_fit(sections, **kwargs)
    return _split_first_fit(sections, **kwargs)


def split_obligations_by_token_budget(
    obligations: list[ContractObligation],
    *,
    batch_size: int,
    max_tokens: int,
    hits_by_obligation: dict[str, list[RetrievalHit]],
    settings: ReviewSettings | None = None,
) -> list[list[ContractObligation]]:
    if not obligations:
        return []
    cfg = settings or get_settings()
    batches: list[list[ContractObligation]] = []
    current: list[ContractObligation] = []
    for obligation in obligations:
        trial = current + [obligation]
        if len(trial) > batch_size:
            if current:
                batches.append(current)
            current = [obligation]
        elif (
            estimate_obligation_batch_tokens(trial, hits_by_obligation, settings=cfg) > max_tokens
            and current
        ):
            batches.append(current)
            current = [obligation]
        else:
            current = trial
    if current:
        batches.append(current)
    return batches


def max_batch_estimated_tokens(
    batches: list[list[IndexedChunk]],
    hits_by_section: dict[str, list[RetrievalHit]],
    *,
    settings: ReviewSettings | None = None,
    playbook_hints_by_document: dict[str, PlaybookHints] | None = None,
    categories_by_section: dict[str, list[str]] | None = None,
    extra_context_by_section: dict[str, str] | None = None,
) -> int:
    if not batches:
        return 0
    cfg = settings or get_settings()
    return max(
        estimate_section_batch_tokens(
            batch,
            hits_by_section,
            settings=cfg,
            playbook_hints_by_document=playbook_hints_by_document,
            categories_by_section=categories_by_section,
            extra_context_by_section=extra_context_by_section,
        )
        for batch in batches
    )
