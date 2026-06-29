"""Final gap verify — re-retrieve, gap LLM, unclear/conflict re-compare."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

from document_core.schemas.chunk import IndexedChunk
from document_core.schemas.compliance import ComplianceFinding, ComplianceStatus
from review_agent.clients.document_client import DocumentMCPClient
from review_agent.config import ReviewSettings, get_settings
from review_agent.models.llm_gateway import get_review_model, invoke_structured
from review_agent.schemas.compliance_llm import ComplianceLLMResult
from review_agent.schemas.section_compare import (
    BatchFinalGapVerifyLLMResult,
    FinalGapVerifyItem,
)
from review_agent.schemas.section_retrieval import SectionRetrievalBundle
from review_agent.services.multi_retrieval import multi_retrieve_for_section
from review_agent.services.async_limits import gather_limited
from review_agent.services.policy_coverage import apply_coverage_gate
from review_agent.services.quote_validate import truncate_section, validate_gap_item_quotes
from review_agent.services.section_compare_llm import compare_section_batch
from review_agent.services.config_advisory import effective_unclear_recompare_max_sections
from review_agent.services.token_budget import (
    effective_compare_max_tokens,
    split_batch_by_token_budget,
)
from review_agent.services.conflict_resolve import (
    emit_skipped_conflict_recompare,
    emit_unresolved_policy_conflict,
)
from review_agent.services.section_merge import section_items_to_findings
from review_agent.services.section_gap_status import upgrade_substantive_gap_finding
from review_agent.services.unclear_recompare import (
    classify_unclear_finding,
    eligible_for_unclear_recompare,
    section_has_grounded_non_compliant,
)

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "final_gap_verify.md"


def _load_gap_prompt_template() -> tuple[str, str]:
    raw = _PROMPT_PATH.read_text(encoding="utf-8")
    if "## SYSTEM" not in raw or "## USER" not in raw:
        raise ValueError("final_gap_verify.md must contain ## SYSTEM and ## USER")
    _, system_block = raw.split("## SYSTEM", 1)
    system_text, user_block = system_block.split("## USER", 1)
    return system_text.strip(), user_block.strip()


def _gap_item_to_finding(item: FinalGapVerifyItem, section: IndexedChunk) -> ComplianceFinding:
    return ComplianceFinding(
        finding_id=str(uuid.uuid4()),
        dimension_id=f"{item.section_id}:final_gap",
        dimension_label=section.title or item.section_id,
        status=item.status,
        severity=item.severity,
        contract_quote=item.contract_quote,
        contract_section_id=item.section_id,
        rationale=item.rationale,
        metadata={
            "compliance_mode": "section_first_final",
            "gap_type": "no_policy",
            "final_verify": "gap_llm",
        },
    )


def _normalize_gap_item(item: FinalGapVerifyItem, section_text: str) -> FinalGapVerifyItem:
    adapted = ComplianceLLMResult(
        status=item.status,
        severity=item.severity,
        contract_quote=item.contract_quote,
        policy_quote="",
        rationale=item.rationale,
    )
    normalized = validate_gap_item_quotes(adapted, contract_text=section_text)
    return item.model_copy(
        update={
            "status": normalized.status,
            "severity": normalized.severity,
            "contract_quote": normalized.contract_quote,
            "rationale": normalized.rationale,
        }
    )


def _format_gaps_block(
    sections: list[IndexedChunk],
    bundles: dict[str, SectionRetrievalBundle],
    *,
    max_chars: int,
) -> str:
    blocks: list[str] = []
    for section in sections:
        bundle = bundles.get(section.section_id)
        categories = ", ".join(bundle.categories) if bundle and bundle.categories else "general"
        blocks.append(f"### Section {section.section_id} — {section.title}")
        blocks.append(
            f"Prior status: NO_POLICY (no playbook retrieved after expanded search)\n"
            f"Categories tried: {categories}"
        )
        blocks.append(f"```\n{truncate_section(section.text or '', max_chars)}\n```")
    return "\n\n".join(blocks)


def _finding_ids_for_section(
    findings: list[ComplianceFinding],
    section_id: str,
    *,
    gap_types: frozenset[str] | None = None,
) -> list[str]:
    allowed = gap_types or frozenset({"no_policy"})
    return [
        f.finding_id
        for f in findings
        if f.contract_section_id == section_id
        and f.metadata.get("gap_type") in allowed
    ]


def _split_gap_section_ids(
    gap_section_ids: list[str],
    *,
    no_policy_gap_ids: list[str] | None,
    compare_omitted_gap_ids: list[str] | None,
    existing: list[ComplianceFinding],
    bundles: dict[str, SectionRetrievalBundle],
) -> tuple[list[str], list[str]]:
    if no_policy_gap_ids is not None and compare_omitted_gap_ids is not None:
        return list(no_policy_gap_ids), list(compare_omitted_gap_ids)

    no_policy: list[str] = []
    compare_omitted: list[str] = []
    gap_set = set(gap_section_ids)
    for finding in existing:
        sid = finding.contract_section_id
        if not sid or sid not in gap_set:
            continue
        gap_type = finding.metadata.get("gap_type")
        if gap_type == "compare_omitted" and sid not in compare_omitted:
            compare_omitted.append(sid)
        elif gap_type == "no_policy" and sid not in no_policy:
            no_policy.append(sid)

    for sid in gap_section_ids:
        if sid in no_policy or sid in compare_omitted:
            continue
        bundle = bundles.get(sid)
        if bundle and bundle.policy_hits:
            compare_omitted.append(sid)
        else:
            no_policy.append(sid)
    return no_policy, compare_omitted


def _conflict_context_by_section(
    pairs: list[tuple[str, str]],
    findings_map: dict[str, ComplianceFinding],
) -> dict[str, str]:
    by_section: dict[str, list[str]] = {}
    for left_id, right_id in pairs:
        for fid in (left_id, right_id):
            finding = findings_map.get(fid)
            if finding is None or not finding.contract_section_id:
                continue
            sid = finding.contract_section_id
            line = (
                f"- [{finding.status.value}] {finding.dimension_label}: "
                f"{finding.rationale[:500]}"
            )
            by_section.setdefault(sid, [])
            if line not in by_section[sid]:
                by_section[sid].append(line)
    return {
        sid: "Prior conflicting assessments (resolve to one status per dimension):\n"
        + "\n".join(lines)
        for sid, lines in by_section.items()
    }


async def verify_gap_sections_llm(
    sections: list[IndexedChunk],
    bundles: dict[str, SectionRetrievalBundle],
    *,
    contract_type: str | None,
    settings: ReviewSettings,
) -> tuple[list[ComplianceFinding], list[str], int]:
    if not sections:
        return [], [], 0

    warnings: list[str] = []
    max_chars = settings.section_compare_max_section_chars
    batch_size = max(1, settings.section_compare_batch_size)
    system_tpl, user_tpl = _load_gap_prompt_template()
    model = get_review_model(
        temperature=settings.compliance_llm_temperature,
        max_tokens=settings.compliance_llm_max_tokens,
    )

    findings: list[ComplianceFinding] = []
    failed = 0
    section_text_by_id = {s.section_id: s.text or "" for s in sections}

    for start in range(0, len(sections), batch_size):
        batch = sections[start : start + batch_size]
        gaps_block = _format_gaps_block(batch, bundles, max_chars=max_chars)
        user = user_tpl.format(
            contract_type=(contract_type or "unknown").strip() or "unknown",
            gaps_block=gaps_block,
        )
        try:
            result = await invoke_structured(
                model,
                BatchFinalGapVerifyLLMResult,
                system=system_tpl,
                user=user,
            )
        except Exception as exc:  # noqa: BLE001
            failed += len(batch)
            warnings.append(f"gap LLM failed for batch starting {batch[0].section_id}: {exc}")
            continue

        if not result.items:
            failed += len(batch)
            warnings.append(
                f"gap LLM returned no items for batch starting {batch[0].section_id}"
            )
            continue

        batch_ids = {s.section_id for s in batch}
        for item in result.items:
            if item.section_id not in batch_ids:
                warnings.append(f"gap LLM returned unknown section_id {item.section_id!r}")
                continue
            section = next(s for s in batch if s.section_id == item.section_id)
            normalized = _normalize_gap_item(item, section_text_by_id[item.section_id])
            finding = _gap_item_to_finding(normalized, section)
            findings.append(
                upgrade_substantive_gap_finding(finding, section, settings=settings)
            )

    return findings, warnings, failed


def _resolve_recompare_finding_ids(
    unclear_ids: list[str],
    recompare_ids: list[str] | None,
    findings_map: dict[str, ComplianceFinding],
) -> list[str]:
    if recompare_ids is not None:
        return [fid for fid in recompare_ids if fid in findings_map]
    return [
        fid
        for fid in unclear_ids
        if fid in findings_map and eligible_for_unclear_recompare(findings_map[fid])
    ]


def _lowest_confidence_by_section(
    finding_ids: list[str],
    findings_map: dict[str, ComplianceFinding],
) -> dict[str, float]:
    scores: dict[str, float] = {}
    for fid in finding_ids:
        finding = findings_map[fid]
        sid = finding.contract_section_id
        if not sid:
            continue
        raw = (finding.metadata or {}).get("confidence")
        conf = float(raw) if raw is not None else 1.0
        scores[sid] = min(scores.get(sid, 1.0), conf)
    return scores


def _categories_by_section(
    sections: list[IndexedChunk],
    bundles: dict[str, SectionRetrievalBundle],
) -> dict[str, list[str]]:
    return {
        s.section_id: list(bundles[s.section_id].categories) if bundles.get(s.section_id) else []
        for s in sections
    }


def _split_sections_for_compare(
    sections: list[IndexedChunk],
    hits_map: dict[str, list],
    bundles: dict[str, SectionRetrievalBundle],
    cfg: ReviewSettings,
    *,
    extra_context_by_section: dict[str, str] | None = None,
) -> list[tuple[list[IndexedChunk], dict[str, list]]]:
    """Token-aware compare batches with per-batch hit maps (Phase D)."""
    if not sections:
        return []
    categories = _categories_by_section(sections, bundles)
    batches = split_batch_by_token_budget(
        sections,
        batch_size=cfg.section_compare_batch_size,
        max_tokens=effective_compare_max_tokens(cfg.section_compare_max_tokens, cfg),
        bundles=hits_map,
        settings=cfg,
        categories_by_section=categories,
        extra_context_by_section=extra_context_by_section,
    )
    return [
        (batch, {s.section_id: hits_map.get(s.section_id, []) for s in batch})
        for batch in batches
    ]


async def _compare_sections_gated(
    sections: list[IndexedChunk],
    hits_map: dict[str, list],
    bundles: dict[str, SectionRetrievalBundle],
    *,
    contract_type: str | None,
    memory_context: str,
    settings: ReviewSettings,
) -> tuple[list, list, list[str]]:
    """Run coverage gate then compare; return (compare_items, ipc_items, warnings)."""
    from review_agent.schemas.section_compare import SectionCompareItem

    categories_by_section = {
        s.section_id: list(bundles[s.section_id].categories) if bundles.get(s.section_id) else []
        for s in sections
    }
    ipc_items: list[SectionCompareItem] = []
    warnings: list[str] = []
    working_hits = dict(hits_map)
    if settings.policy_coverage_enabled:
        # Re-retrieved hits: full coverage filter (no retrieval_gate_applied meta).
        working_hits, ipc_items, cov_warnings = apply_coverage_gate(
            sections,
            working_hits,
            categories_by_section,
            settings=settings,
            retrieval_gate_applied_by_section={},
        )
        warnings.extend(cov_warnings)
    compare_sections = [s for s in sections if working_hits.get(s.section_id)]
    items = []
    if compare_sections:
        batch_hits = {s.section_id: working_hits[s.section_id] for s in compare_sections}
        batch_items, item_warnings = await compare_section_batch(
            compare_sections,
            batch_hits,
            contract_type=contract_type,
            memory_context=memory_context,
            settings=settings,
            categories_by_section=categories_by_section,
        )
        warnings.extend(item_warnings)
        items = batch_items
    return items, ipc_items, warnings


async def run_final_gap_verify(
    *,
    client: DocumentMCPClient,
    tenant_id: str,
    sections_by_id: dict[str, IndexedChunk],
    bundles: dict[str, SectionRetrievalBundle],
    gap_section_ids: list[str],
    no_policy_gap_ids: list[str] | None = None,
    compare_omitted_gap_ids: list[str] | None = None,
    unclear_finding_ids: list[str] | None = None,
    unclear_recompare_finding_ids: list[str] | None = None,
    conflict_pairs: list[tuple[str, str]] | None = None,
    existing_findings: list[ComplianceFinding] | None = None,
    contract_type: str | None,
    policy_type: str | None,
    memory_context: str = "",
    settings: ReviewSettings | None = None,
) -> tuple[list[ComplianceFinding], list[str], dict[str, Any], list[str]]:
    """Re-retrieve gaps, gap LLM, unclear re-compare, conflict re-compare."""
    cfg = settings or get_settings()
    unclear_ids = list(unclear_finding_ids or [])
    pairs = list(conflict_pairs or [])
    existing = list(existing_findings or [])

    if not cfg.final_gap_verify_enabled:
        return [], [], {"skipped": True, "reason": "disabled"}, []

    if not gap_section_ids and not unclear_ids and not pairs:
        return [], [], {"skipped": True, "reason": "empty_work_queue"}, []

    no_policy_ids, compare_omitted_ids = _split_gap_section_ids(
        gap_section_ids,
        no_policy_gap_ids=no_policy_gap_ids,
        compare_omitted_gap_ids=compare_omitted_gap_ids,
        existing=existing,
        bundles=bundles,
    )
    gap_supersede_types = frozenset({"no_policy", "compare_omitted"})
    recall_settings = cfg.model_copy(
        update={"retrieval_recall_top_k": cfg.final_gap_recall_top_k}
    )
    new_findings: list[ComplianceFinding] = []
    warnings: list[str] = []
    superseded_ids: list[str] = []
    findings_map = {f.finding_id: f for f in existing}

    stats: dict[str, Any] = {
        "gap_sections": len(gap_section_ids),
        "unclear_findings": len(unclear_ids),
        "unclear_recompare_eligible": 0,
        "unclear_recompare_gap_routed": 0,
        "unclear_recompare_ineligible": 0,
        "unclear_recompare_skipped": 0,
        "unclear_recompare_capped": 0,
        "conflict_pairs": len(pairs),
        "re_retrieved": 0,
        "resolved_with_policy": 0,
        "gap_llm_sections": 0,
        "gap_llm_failed": 0,
        "unclear_recompared": 0,
        "conflicts_recompared": 0,
        "conflicts_unresolved": 0,
        "compare_omitted_recovered": 0,
        "gap_recompare_batches": 0,
        "conflict_recompare_batches": 0,
        "coverage_gate_recompare_candidates": 0,
        "coverage_gate_recompare_attempted": 0,
        "coverage_gate_recompare_resolved": 0,
    }

    recompare_finding_ids = _resolve_recompare_finding_ids(
        unclear_ids,
        unclear_recompare_finding_ids,
        findings_map,
    )
    stats["unclear_recompare_eligible"] = len(recompare_finding_ids)
    for fid in unclear_ids:
        finding = findings_map.get(fid)
        if finding is None:
            continue
        reason = classify_unclear_finding(finding)
        if reason == "gap_context":
            stats["unclear_recompare_gap_routed"] += 1
        elif not eligible_for_unclear_recompare(finding):
            stats["unclear_recompare_ineligible"] += 1

    # Phase 1: re-retrieve sections with no policy hits (parallel, PF-1B-4)
    retrieve_targets: list[tuple[str, IndexedChunk]] = []
    for section_id in no_policy_ids:
        section = sections_by_id.get(section_id)
        if section is None:
            continue
        bundle = bundles.get(section_id)
        if bundle and bundle.policy_hits:
            continue
        retrieve_targets.append((section_id, section))

    async def _re_retrieve_section(
        section_id: str,
        section: IndexedChunk,
    ) -> tuple[str, SectionRetrievalBundle | None, str | None]:
        try:
            refreshed = await multi_retrieve_for_section(
                client,
                tenant_id=tenant_id,
                section=section,
                contract_type=contract_type,
                policy_type=policy_type,
                settings=recall_settings,
            )
            return section_id, refreshed, None
        except Exception as exc:  # noqa: BLE001
            return section_id, None, str(exc)

    if retrieve_targets:
        retrieve_results = await gather_limited(
            [_re_retrieve_section(section_id, section) for section_id, section in retrieve_targets],
            limit=cfg.section_retrieval_concurrency,
        )
        recompare_after_retrieve: list[str] = []
        for item in retrieve_results:
            if isinstance(item, BaseException):
                continue
            section_id, refreshed, err = item
            if err:
                warnings.append(f"final gap re-retrieve failed for {section_id}: {err}")
                continue
            if refreshed is None:
                continue

            stats["re_retrieved"] += 1
            bundles[section_id] = refreshed

            if not refreshed.policy_hits:
                continue

            stats["resolved_with_policy"] += 1
            recompare_after_retrieve.append(section_id)

        if recompare_after_retrieve:
            batch_sections_list = [
                sections_by_id[sid] for sid in recompare_after_retrieve if sid in sections_by_id
            ]
            hits_full = {
                sid: list(bundles[sid].policy_hits)
                for sid in recompare_after_retrieve
                if bundles.get(sid) and bundles[sid].policy_hits
            }
            token_batches = _split_sections_for_compare(
                batch_sections_list,
                hits_full,
                bundles,
                cfg,
            )
            stats["gap_recompare_batches"] += len(token_batches)
            for batch_sections, hits_map in token_batches:
                if not hits_map:
                    continue
                batch_sids = [s.section_id for s in batch_sections]
                items, ipc_items, item_warnings = await _compare_sections_gated(
                    batch_sections,
                    hits_map,
                    bundles,
                    contract_type=contract_type,
                    memory_context=memory_context,
                    settings=cfg,
                )
                warnings.extend(item_warnings)
                all_items = list(ipc_items) + list(items)
                phase_findings = section_items_to_findings(
                    all_items, pipeline="section_first_final"
                )
                new_findings.extend(phase_findings)
                for sid in batch_sids:
                    superseded_ids.extend(
                        _finding_ids_for_section(
                            existing, sid, gap_types=gap_supersede_types
                        )
                    )

    resolved_gap_ids = {f.contract_section_id for f in new_findings if f.contract_section_id}
    if resolved_gap_ids:
        warnings.append(
            f"final gap verify resolved {len(resolved_gap_ids)} section(s) after re-retrieve"
        )

    # Phase 1b: re-compare sections where policy was retrieved but compare omitted
    compare_omitted_to_run = [
        sid
        for sid in compare_omitted_ids
        if sid in sections_by_id
        and sid not in resolved_gap_ids
        and bundles.get(sid)
        and bundles[sid].policy_hits
    ]
    if compare_omitted_to_run:
        batch_sections_list = [sections_by_id[sid] for sid in compare_omitted_to_run]
        hits_full = {
            sid: list(bundles[sid].policy_hits)
            for sid in compare_omitted_to_run
            if bundles.get(sid) and bundles[sid].policy_hits
        }
        token_batches = _split_sections_for_compare(
            batch_sections_list,
            hits_full,
            bundles,
            cfg,
        )
        for batch_sections, hits_map in token_batches:
            if not hits_map:
                continue
            batch_sids = [s.section_id for s in batch_sections]
            items, ipc_items, item_warnings = await _compare_sections_gated(
                batch_sections,
                hits_map,
                bundles,
                contract_type=contract_type,
                memory_context=memory_context,
                settings=cfg,
            )
            warnings.extend(item_warnings)
            all_items = list(ipc_items) + list(items)
            if all_items:
                stats["compare_omitted_recovered"] += len(batch_sids)
                new_findings.extend(
                    section_items_to_findings(all_items, pipeline="section_first_final")
                )
                for sid in batch_sids:
                    superseded_ids.extend(
                        _finding_ids_for_section(
                            existing,
                            sid,
                            gap_types=gap_supersede_types,
                        )
                    )
        resolved_gap_ids = {f.contract_section_id for f in new_findings if f.contract_section_id}
        if stats["compare_omitted_recovered"]:
            warnings.append(
                f"final gap verify re-compared {stats['compare_omitted_recovered']} "
                "compare-omitted section(s)"
            )

    # Phase 2: gap LLM for no-policy sections still without hits
    still_gap_ids = [
        sid
        for sid in no_policy_ids
        if sid in sections_by_id
        and sid not in resolved_gap_ids
        and not (bundles.get(sid) and bundles[sid].policy_hits)
    ]
    if still_gap_ids:
        gap_sections = [sections_by_id[sid] for sid in still_gap_ids]
        gap_findings, gap_warnings, gap_failed = await verify_gap_sections_llm(
            gap_sections,
            bundles,
            contract_type=contract_type,
            settings=cfg,
        )
        warnings.extend(gap_warnings)
        stats["gap_llm_sections"] = len(gap_findings)
        stats["gap_llm_failed"] = gap_failed
        for finding in gap_findings:
            if finding.contract_section_id:
                superseded_ids.extend(
                    _finding_ids_for_section(
                        existing,
                        finding.contract_section_id,
                        gap_types=gap_supersede_types,
                    )
                )
        new_findings.extend(gap_findings)
        resolved_gap_ids.update(
            f.contract_section_id for f in gap_findings if f.contract_section_id
        )

    gap_llm_section_ids = {
        f.contract_section_id
        for f in new_findings
        if f.metadata.get("final_verify") == "gap_llm" and f.contract_section_id
    }

    # Phase 3: unclear re-compare (eligible playbook rows, capped + batched)
    recompare_finding_id_set = set(recompare_finding_ids)
    unclear_sections: dict[str, IndexedChunk] = {}
    unclear_supersede: dict[str, list[str]] = {}
    if cfg.final_verify_unclear_recompare_enabled and recompare_finding_ids:
        conf_by_section = _lowest_confidence_by_section(recompare_finding_ids, findings_map)
        candidate_section_ids: list[str] = []
        coverage_gate_by_section: dict[str, bool] = {}
        for fid in recompare_finding_ids:
            finding = findings_map.get(fid)
            if finding is None or not finding.contract_section_id:
                continue
            sid = finding.contract_section_id
            is_coverage_gate = classify_unclear_finding(finding) == "coverage_gate_ipc"
            if is_coverage_gate:
                stats["coverage_gate_recompare_candidates"] += 1
            if sid in gap_llm_section_ids:
                continue
            bundle = bundles.get(sid)
            if not bundle or not bundle.policy_hits:
                continue
            section = sections_by_id.get(sid)
            if section is None:
                continue
            if section_has_grounded_non_compliant(sid, existing):
                stats["unclear_recompare_skipped"] += 1
                continue
            if sid not in unclear_sections:
                unclear_sections[sid] = section
                candidate_section_ids.append(sid)
            if is_coverage_gate:
                coverage_gate_by_section[sid] = True
            unclear_supersede.setdefault(sid, [])
            if fid not in unclear_supersede[sid]:
                unclear_supersede[sid].append(fid)

        candidate_section_ids.sort(key=lambda sid: conf_by_section.get(sid, 1.0))
        reviewable_count = len(sections_by_id)
        max_sections = effective_unclear_recompare_max_sections(
            cfg,
            reviewable_sections=reviewable_count,
        )
        stats["unclear_recompare_cap_effective"] = max_sections
        stats["unclear_recompare_cap_mode"] = cfg.final_verify_unclear_recompare_cap_mode
        sections_to_recompare = candidate_section_ids[:max_sections]
        for sid in sections_to_recompare:
            if coverage_gate_by_section.get(sid):
                stats["coverage_gate_recompare_attempted"] += 1
        if len(candidate_section_ids) > max_sections:
            stats["unclear_recompare_capped"] = len(candidate_section_ids) - max_sections
            warnings.append(
                f"unclear re-compare capped at {max_sections} section(s) "
                f"({len(candidate_section_ids)} eligible)."
            )

        batch_sections_list = [unclear_sections[sid] for sid in sections_to_recompare]
        hits_full = {
            sid: list(bundles[sid].policy_hits)
            for sid in sections_to_recompare
            if bundles.get(sid) and bundles[sid].policy_hits
        }
        token_batches = _split_sections_for_compare(
            batch_sections_list,
            hits_full,
            bundles,
            cfg,
        )
        for batch_sections, hits_map in token_batches:
            if not hits_map:
                continue
            batch_sids = [s.section_id for s in batch_sections]
            items, item_warnings = await compare_section_batch(
                batch_sections,
                hits_map,
                contract_type=contract_type,
                memory_context=memory_context,
                settings=cfg,
            )
            warnings.extend(item_warnings)
            if items:
                stats["unclear_recompared"] += len(batch_sids)
                for sid in batch_sids:
                    if not coverage_gate_by_section.get(sid):
                        continue
                    section_items = [it for it in items if it.section_id == sid]
                    if any(
                        it.status != ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT
                        for it in section_items
                    ):
                        stats["coverage_gate_recompare_resolved"] += 1
                new_findings.extend(
                    section_items_to_findings(items, pipeline="section_first_final")
                )
                for sid in batch_sids:
                    superseded_ids.extend(
                        fid
                        for fid in unclear_supersede.get(sid, [])
                        if fid in recompare_finding_id_set
                    )
    elif recompare_finding_ids and not cfg.final_verify_unclear_recompare_enabled:
        stats["unclear_recompare_skipped"] = len(recompare_finding_ids)
        warnings.append("unclear re-compare disabled by config.")

    # Phase 4: conflict re-compare
    conflict_context = _conflict_context_by_section(pairs, findings_map)
    conflict_supersede: dict[str, list[str]] = {}
    for left_id, right_id in pairs:
        for fid in (left_id, right_id):
            finding = findings_map.get(fid)
            if finding and finding.contract_section_id:
                sid = finding.contract_section_id
                conflict_supersede.setdefault(sid, [])
                if fid not in conflict_supersede[sid]:
                    conflict_supersede[sid].append(fid)

    conflict_sids_to_run: list[str] = []
    for sid in conflict_context:
        section = sections_by_id.get(sid)
        bundle = bundles.get(sid)
        prior_for_section = [
            findings_map[fid]
            for fid in conflict_supersede.get(sid, [])
            if fid in findings_map
        ]
        if section is None or not bundle or not bundle.policy_hits:
            warnings.append(f"conflict re-compare skipped for {sid}: no policy hits")
            if cfg.conflict_emit_on_skip and prior_for_section:
                skip_row = emit_skipped_conflict_recompare(sid, prior_for_section)
                new_findings.append(skip_row)
                stats["conflicts_unresolved"] += 1
                superseded_ids.extend(conflict_supersede.get(sid, []))
            continue
        conflict_sids_to_run.append(sid)

    if conflict_sids_to_run:
        batch_sections_list = [sections_by_id[sid] for sid in conflict_sids_to_run]
        hits_full = {
            sid: list(bundles[sid].policy_hits)
            for sid in conflict_sids_to_run
            if bundles.get(sid) and bundles[sid].policy_hits
        }
        ctx_full = {sid: conflict_context[sid] for sid in conflict_sids_to_run}
        token_batches = _split_sections_for_compare(
            batch_sections_list,
            hits_full,
            bundles,
            cfg,
            extra_context_by_section=ctx_full,
        )
        stats["conflict_recompare_batches"] += len(token_batches)
        for batch_sections, hits_map in token_batches:
            if not hits_map:
                continue
            batch_sids = [s.section_id for s in batch_sections]
            ctx_by_section = {sid: conflict_context[sid] for sid in batch_sids}
            items, item_warnings = await compare_section_batch(
                batch_sections,
                hits_map,
                contract_type=contract_type,
                memory_context=memory_context,
                extra_context_by_section=ctx_by_section,
                settings=cfg,
            )
            warnings.extend(item_warnings)
            items_by_section: dict[str, list] = {}
            for item in items:
                items_by_section.setdefault(item.section_id, []).append(item)
            for sid in batch_sids:
                section_items = items_by_section.get(sid) or []
                prior_for_section = [
                    findings_map[fid]
                    for fid in conflict_supersede.get(sid, [])
                    if fid in findings_map
                ]
                if not section_items:
                    continue
                stats["conflicts_recompared"] += 1
                new_from_items = section_items_to_findings(
                    section_items, pipeline="section_first_final"
                )
                conflict_row = emit_unresolved_policy_conflict(
                    sid, prior_for_section, new_from_items
                )
                if conflict_row:
                    stats["conflicts_unresolved"] += 1
                    new_findings.append(conflict_row)
                else:
                    new_findings.extend(new_from_items)
                superseded_ids.extend(conflict_supersede.get(sid, []))

    stats["new_findings"] = len(new_findings)
    stats["superseded_count"] = len(set(superseded_ids))
    return new_findings, warnings, stats, list(dict.fromkeys(superseded_ids))
