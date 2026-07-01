"""IPC-3 gated funnel helpers (E-BP2 boilerplate override, funnel identity)."""

from __future__ import annotations

from typing import Any

from review_agent.config import ReviewSettings
from review_agent.schemas.obligation import ContractObligation
from review_agent.schemas.routing_plan import ObligationRoutingPlan


def boilerplate_substantive_override(
    obligation: ContractObligation,
    plan: ObligationRoutingPlan,
    settings: ReviewSettings,
) -> bool:
    """E-BP2 — when enabled, do not boilerplate-skip substantive obligations."""
    if not settings.ipc3_boilerplate_substantive_override_enabled:
        return False
    if plan.routing_source != "skipped_boilerplate":
        return False
    return boilerplate_obligation_routable(obligation, settings)


def boilerplate_obligation_routable(
    obligation: ContractObligation,
    settings: ReviewSettings,
) -> bool:
    """E-BP2 — obligation-level check before skipped_boilerplate plan is assigned."""
    if not settings.ipc3_boilerplate_substantive_override_enabled:
        return False
    if list(obligation.explicit_policy_mentions or []):
        return True
    otype = (obligation.obligation_type or "").strip().lower()
    return bool(otype and otype not in ("boilerplate", "general"))


def check_obligation_funnel_identity(stats: dict[str, Any]) -> list[str]:
    """Verify §2 arithmetic identities on compliance_stats funnel block."""
    errors: list[str] = []
    funnel = stats.get("obligation_pipeline_funnel") or {}
    if not funnel:
        errors.append("missing obligation_pipeline_funnel")
        return errors

    extracted = int(funnel.get("extracted") or stats.get("obligation_count") or 0)
    queued = int(funnel.get("compare_queued") or 0)
    pre_ipc = int(funnel.get("compare_pre_ipc") or 0)
    llm_ipc = int(funnel.get("llm_ipc_count") or 0)
    compared = int(funnel.get("post_validation_compared") or stats.get("obligation_compare_count") or 0)
    llm_returned = int(funnel.get("llm_items_returned") or 0)

    skip = funnel.get("skip_by_reason") or stats.get("obligation_evidence_skip_by_reason") or {}
    if skip:
        pre_from_skip = sum(v for k, v in skip.items() if k != "evidence_sufficient")
        queued_from_skip = int(skip.get("evidence_sufficient") or 0)
        if extracted and pre_from_skip + queued_from_skip != extracted:
            errors.append(
                f"skip_by_reason sum {pre_from_skip}+{queued_from_skip} != extracted {extracted}"
            )

    if extracted and pre_ipc + queued != extracted:
        errors.append(f"PRE_IPC+QUEUED {pre_ipc}+{queued} != extracted {extracted}")
    if queued and llm_returned and llm_returned != queued:
        errors.append(f"llm_items_returned {llm_returned} != compare_queued {queued}")
    if queued and llm_ipc + compared != queued:
        errors.append(f"llm_ipc+compared {llm_ipc}+{compared} != queued {queued}")
    if extracted and pre_ipc + llm_ipc + compared != extracted:
        errors.append(
            f"PRE_IPC+llm_ipc+compared {pre_ipc}+{llm_ipc}+{compared} != extracted {extracted}"
        )
    return errors
