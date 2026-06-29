"""Quote verification with optional LLM repair before MCP gate (P2-7)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from document_core.schemas.chunk import GetSectionRequest, GroundingCheckRequest
from document_core.schemas.compliance import ComplianceFinding, ComplianceStatus
from review_agent.clients.document_client import DocumentMCPClient
from review_agent.config import ReviewSettings
from review_agent.schemas.quote_repair import QuoteRepairResult
from review_agent.services.quote_repair_llm import QuoteRepairJob, repair_quotes_batch


def _grounded_in_requested_section(
    check: Any,
    section_id: str,
) -> bool:
    if not check.grounded:
        return False
    requested = (section_id or "").strip()
    if not requested:
        return True
    matched = (check.section_id or "").strip()
    if not matched:
        return True
    return matched == requested


async def _fetch_section_text(
    client: DocumentMCPClient,
    *,
    tenant_id: str,
    document_id: UUID,
    section_id: str,
) -> str:
    if not section_id:
        return ""
    chunk = await client.get_section(
        GetSectionRequest(
            tenant_id=tenant_id,
            document_id=document_id,
            section_id=section_id,
        )
    )
    return (chunk.text or "").strip() if chunk else ""


async def verify_quote_with_repair(
    client: DocumentMCPClient,
    *,
    tenant_id: str,
    document_id: UUID,
    quote: str,
    section_id: str,
    settings: ReviewSettings,
    stats: dict[str, int],
    verify_fn: Any,
    skip_repair: bool = False,
    repaired_quote: str | None = None,
) -> tuple[str, bool, dict[str, Any]]:
    """Verify quote via MCP; on failure optionally repair from section text and re-verify."""
    candidate = (repaired_quote or quote or "").strip()
    meta: dict[str, Any] = {}
    if not candidate:
        return "", True, meta

    check = await verify_fn(
        GroundingCheckRequest(
            tenant_id=tenant_id,
            document_id=document_id,
            quote=candidate,
            section_id=section_id,
        )
    )
    if _grounded_in_requested_section(check, section_id):
        if repaired_quote and repaired_quote != (quote or "").strip():
            meta["quote_repair_used"] = True
        return candidate, True, meta
    if check.grounded:
        meta["grounding_section_mismatch"] = True
        if check.section_id:
            meta["grounding_matched_section_id"] = check.section_id
        return candidate, False, meta

    if skip_repair or not settings.quote_repair_enabled:
        return candidate, False, meta

    source_text = await _fetch_section_text(
        client,
        tenant_id=tenant_id,
        document_id=document_id,
        section_id=section_id,
    )
    if not source_text:
        meta["grounding_repair_attempted"] = True
        stats["quote_repair_attempts"] = stats.get("quote_repair_attempts", 0) + 1
        return candidate, False, meta

    stats["quote_repair_attempts"] = stats.get("quote_repair_attempts", 0) + 1
    meta["grounding_repair_attempted"] = True
    from review_agent.services.quote_repair_llm import repair_quote_for_section

    repair = await repair_quote_for_section(
        source_text=source_text,
        candidate_quote=candidate,
        section_id=section_id,
        settings=settings,
    )
    repaired = (repair.repaired_quote or "").strip()
    if not repaired:
        return candidate, False, meta

    recheck = await verify_fn(
        GroundingCheckRequest(
            tenant_id=tenant_id,
            document_id=document_id,
            quote=repaired,
            section_id=section_id,
        )
    )
    if _grounded_in_requested_section(recheck, section_id):
        meta["quote_repair_used"] = True
        if repair.repair_notes:
            meta["quote_repair_notes"] = repair.repair_notes[:200]
        stats["quote_repair_success"] = stats.get("quote_repair_success", 0) + 1
        return repaired, True, meta

    if recheck.grounded:
        meta["grounding_section_mismatch"] = True
        if recheck.section_id:
            meta["grounding_matched_section_id"] = recheck.section_id

    return candidate, False, meta


@dataclass
class _QuoteSideState:
    quote: str
    ok: bool = True
    meta: dict[str, Any] = field(default_factory=dict)
    repair_id: str | None = None
    repair_job: QuoteRepairJob | None = None


@dataclass
class _FindingGroundState:
    finding: ComplianceFinding
    contract: _QuoteSideState | None = None
    policy: _QuoteSideState | None = None


async def _verify_side(
    client: DocumentMCPClient,
    *,
    tenant_id: str,
    document_id: UUID,
    quote: str,
    section_id: str,
    settings: ReviewSettings,
    verify_fn: Any,
    skip_repair: bool,
    defer_repair: bool,
    repair_id: str,
) -> _QuoteSideState:
    candidate = (quote or "").strip()
    if not candidate:
        return _QuoteSideState(quote="", ok=True)

    check = await verify_fn(
        GroundingCheckRequest(
            tenant_id=tenant_id,
            document_id=document_id,
            quote=candidate,
            section_id=section_id,
        )
    )
    if _grounded_in_requested_section(check, section_id):
        return _QuoteSideState(quote=candidate, ok=True)

    meta: dict[str, Any] = {}
    if check.grounded:
        meta["grounding_section_mismatch"] = True
        if check.section_id:
            meta["grounding_matched_section_id"] = check.section_id
        return _QuoteSideState(quote=candidate, ok=False, meta=meta)

    if skip_repair or not settings.quote_repair_enabled:
        return _QuoteSideState(quote=candidate, ok=False, meta=meta)

    source_text = await _fetch_section_text(
        client,
        tenant_id=tenant_id,
        document_id=document_id,
        section_id=section_id,
    )
    if not source_text:
        meta["grounding_repair_attempted"] = True
        return _QuoteSideState(quote=candidate, ok=False, meta=meta)

    meta["grounding_repair_attempted"] = True
    if defer_repair:
        job = QuoteRepairJob(
            repair_id=repair_id,
            section_id=section_id,
            source_text=source_text,
            candidate_quote=candidate,
        )
        return _QuoteSideState(
            quote=candidate,
            ok=False,
            meta=meta,
            repair_id=repair_id,
            repair_job=job,
        )

    from review_agent.services.quote_repair_llm import repair_quote_for_section

    repair = await repair_quote_for_section(
        source_text=source_text,
        candidate_quote=candidate,
        section_id=section_id,
        settings=settings,
    )
    repaired = (repair.repaired_quote or "").strip()
    if not repaired:
        return _QuoteSideState(quote=candidate, ok=False, meta=meta)

    recheck = await verify_fn(
        GroundingCheckRequest(
            tenant_id=tenant_id,
            document_id=document_id,
            quote=repaired,
            section_id=section_id,
        )
    )
    if _grounded_in_requested_section(recheck, section_id):
        meta["quote_repair_used"] = True
        if repair.repair_notes:
            meta["quote_repair_notes"] = repair.repair_notes[:200]
        return _QuoteSideState(quote=repaired, ok=True, meta=meta)

    if recheck.grounded:
        meta["grounding_section_mismatch"] = True
        if recheck.section_id:
            meta["grounding_matched_section_id"] = recheck.section_id
    return _QuoteSideState(quote=candidate, ok=False, meta=meta)


async def _apply_repair_to_side(
    client: DocumentMCPClient,
    *,
    tenant_id: str,
    document_id: UUID,
    section_id: str,
    side: _QuoteSideState,
    repaired_map: dict[str, Any],
    stats: dict[str, int],
    verify_fn: Any,
) -> _QuoteSideState:
    if side.ok or side.repair_id is None:
        return side
    repair = repaired_map.get(side.repair_id)
    if repair is None:
        stats["quote_repair_attempts"] = stats.get("quote_repair_attempts", 0) + 1
        return side

    stats["quote_repair_attempts"] = stats.get("quote_repair_attempts", 0) + 1
    repaired = (repair.repaired_quote or "").strip()
    meta = dict(side.meta)
    if not repaired:
        return _QuoteSideState(quote=side.quote, ok=False, meta=meta)

    recheck = await verify_fn(
        GroundingCheckRequest(
            tenant_id=tenant_id,
            document_id=document_id,
            quote=repaired,
            section_id=section_id,
        )
    )
    if _grounded_in_requested_section(recheck, section_id):
        meta["quote_repair_used"] = True
        if repair.repair_notes:
            meta["quote_repair_notes"] = repair.repair_notes[:200]
        stats["quote_repair_success"] = stats.get("quote_repair_success", 0) + 1
        return _QuoteSideState(quote=repaired, ok=True, meta=meta)

    if recheck.grounded:
        meta["grounding_section_mismatch"] = True
        if recheck.section_id:
            meta["grounding_matched_section_id"] = recheck.section_id
    return _QuoteSideState(quote=side.quote, ok=False, meta=meta)


async def ground_findings_quotes(
    client: DocumentMCPClient,
    *,
    tenant_id: str,
    contract_document_id: UUID,
    findings: list[ComplianceFinding],
    settings: ReviewSettings,
) -> tuple[list[ComplianceFinding], dict[str, int]]:
    """Verify and optionally batch-repair quotes for all findings."""
    stats: dict[str, int] = {
        "quote_repair_attempts": 0,
        "quote_repair_success": 0,
        "quote_repair_batch_calls": 0,
    }
    use_batch_repair = settings.quote_repair_enabled and settings.quote_repair_batch_enabled
    states: list[_FindingGroundState] = []
    repair_jobs: list[QuoteRepairJob] = []

    for finding in findings:
        if finding.status.value == "INSUFFICIENT_POLICY_CONTEXT":
            states.append(_FindingGroundState(finding=finding))
            continue
        if not finding.contract_quote and not finding.policy_quote:
            states.append(_FindingGroundState(finding=finding))
            continue

        compare_validated = bool((finding.metadata or {}).get("quote_validated_at_compare"))
        skip_repair = compare_validated and settings.grounding_skip_compare_validated_quotes
        defer_repair = use_batch_repair and not skip_repair

        gs = _FindingGroundState(finding=finding)
        if finding.contract_quote:
            gs.contract = await _verify_side(
                client,
                tenant_id=tenant_id,
                document_id=contract_document_id,
                quote=finding.contract_quote,
                section_id=finding.contract_section_id or "",
                settings=settings,
                verify_fn=client.verify_quote,
                skip_repair=skip_repair,
                defer_repair=defer_repair,
                repair_id=f"{finding.finding_id}:contract",
            )
            if gs.contract.repair_job is not None:
                repair_jobs.append(gs.contract.repair_job)

        if finding.policy_quote and finding.policy_document_id:
            gs.policy = await _verify_side(
                client,
                tenant_id=tenant_id,
                document_id=finding.policy_document_id,
                quote=finding.policy_quote,
                section_id=finding.policy_section_id or "",
                settings=settings,
                verify_fn=client.verify_policy_quote,
                skip_repair=skip_repair,
                defer_repair=defer_repair,
                repair_id=f"{finding.finding_id}:policy",
            )
            if gs.policy.repair_job is not None:
                repair_jobs.append(gs.policy.repair_job)

        states.append(gs)

    repaired_map: dict[str, QuoteRepairResult] = {}
    if repair_jobs:
        repaired_map = await repair_quotes_batch(repair_jobs, settings=settings, stats=stats)

    for gs in states:
        if gs.contract and gs.contract.repair_job is not None:
            gs.contract = await _apply_repair_to_side(
                client,
                tenant_id=tenant_id,
                document_id=contract_document_id,
                section_id=gs.finding.contract_section_id or "",
                side=gs.contract,
                repaired_map=repaired_map,
                stats=stats,
                verify_fn=client.verify_quote,
            )
        if gs.policy and gs.policy.repair_job is not None:
            gs.policy = await _apply_repair_to_side(
                client,
                tenant_id=tenant_id,
                document_id=gs.finding.policy_document_id,
                section_id=gs.finding.policy_section_id or "",
                side=gs.policy,
                repaired_map=repaired_map,
                stats=stats,
                verify_fn=client.verify_policy_quote,
            )

    return states, stats


def finalize_grounded_finding(
    gs: _FindingGroundState,
    *,
    settings: ReviewSettings,
    warnings: list[str],
) -> ComplianceFinding | None:
    """Apply grounding outcomes and downgrade rules for one finding."""
    finding = gs.finding
    if finding.status.value == "INSUFFICIENT_POLICY_CONTEXT":
        return finding.model_copy(update={"grounded": True})

    if gs.contract is None and gs.policy is None:
        return None

    contract_quote = gs.contract.quote if gs.contract else finding.contract_quote
    policy_quote = gs.policy.quote if gs.policy else finding.policy_quote
    contract_ok = gs.contract.ok if gs.contract else True
    policy_ok = gs.policy.ok if gs.policy else True
    quote_meta: dict[str, Any] = {}
    if gs.contract:
        quote_meta.update(gs.contract.meta)
    if gs.policy:
        quote_meta.update(gs.policy.meta)

    ok = contract_ok and policy_ok
    if (
        not ok
        and settings.grounding_relax_compliant_empty_policy
        and finding.status == ComplianceStatus.COMPLIANT
        and contract_ok
        and not (finding.policy_quote or "").strip()
    ):
        from review_agent.services.quote_validate import allows_compliant_without_policy_quote

        if allows_compliant_without_policy_quote(
            finding.status,
            finding.rationale,
            contract_ok=contract_ok,
        ):
            ok = True
            policy_ok = True

    if ok:
        meta = dict(finding.metadata or {})
        meta.update(quote_meta)
        return finding.model_copy(
            update={
                "grounded": True,
                "contract_quote": contract_quote,
                "policy_quote": policy_quote,
                "metadata": meta,
            }
        )

    if settings.grounding_downgrade_mode == "keep_status_flag":
        meta = dict(finding.metadata or {})
        meta.update(quote_meta)
        meta["grounding_failed"] = True
        warnings.append(f"finding ungrounded (status kept): {finding.dimension_label}")
        return finding.model_copy(
            update={
                "grounded": False,
                "contract_quote": contract_quote if contract_ok else "",
                "policy_quote": policy_quote if policy_ok else "",
                "metadata": meta,
            }
        )

    if settings.grounding_downgrade_not_drop:
        meta = dict(finding.metadata or {})
        meta.update(quote_meta)
        meta["grounding_failed"] = True
        meta["prior_status"] = finding.status.value
        warnings.append(
            f"finding downgraded to INCONCLUSIVE (grounding failed): {finding.dimension_label}"
        )
        return finding.model_copy(
            update={
                "status": ComplianceStatus.INCONCLUSIVE,
                "grounded": False,
                "metadata": meta,
                "contract_quote": contract_quote if contract_ok else "",
                "policy_quote": policy_quote if policy_ok else "",
            }
        )

    warnings.append(f"finding dropped (grounding failed): {finding.dimension_label}")
    return None
