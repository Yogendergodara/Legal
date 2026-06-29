"""Semantic routing planner LLM (Phase R2)."""

from __future__ import annotations

import logging
from pathlib import Path

from review_agent.config import ReviewSettings, get_settings
from review_agent.models.llm_gateway import get_review_model, invoke_structured
from review_agent.schemas.obligation import ContractObligation
from review_agent.schemas.routing_plan import BatchRoutingPlanResult, ObligationRoutingPlan
from review_agent.services.catalog_registry import CatalogEntry
from review_agent.services.routing_cache import get_cached_plan, plan_cache_key, set_cached_plan
from review_agent.services.quote_validate import truncate_section
from review_agent.services.routing_limits import increment_planner_calls, planner_calls
from review_agent.observability import metrics

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "semantic_routing_planner.md"


def _split_prompt(raw: str) -> tuple[str, str]:
    parts = raw.split("## USER", 1)
    system = parts[0].replace("## SYSTEM", "").strip()
    user = parts[1].strip() if len(parts) > 1 else ""
    return system, user


def _policy_titles_block(catalog_entries: list[CatalogEntry]) -> str:
    titles = [entry.title for entry in catalog_entries if entry.title]
    if not titles:
        return "(none indexed)"
    return "\n".join(f"- {title}" for title in titles[:40])


def _obligations_block(obligations: list[ContractObligation], max_chars: int) -> str:
    blocks: list[str] = []
    for ob in obligations:
        text = truncate_section(ob.text or "", max_chars)
        mentions = ", ".join(ob.explicit_policy_mentions) or "[]"
        blocks.append(
            f"### {ob.obligation_id}\n"
            f"Section: {ob.section_id}\n"
            f"Text: {text}\n"
            f"Explicit policy mentions: {mentions}\n"
        )
    return "\n".join(blocks)


def _apply_planner_confidence_floor(
    ob: ContractObligation,
    confidence: float,
    settings: ReviewSettings,
) -> float:
    """PR-06 — obligations citing a named policy must not planner-IPC at ≤0.3."""
    if ob.explicit_policy_mentions and confidence < settings.routing_planner_explicit_mention_confidence_floor:
        return settings.routing_planner_explicit_mention_confidence_floor
    return confidence


def _fallback_plan(
    ob: ContractObligation,
    *,
    settings: ReviewSettings | None = None,
) -> ObligationRoutingPlan:
    cfg = settings or get_settings()
    words = (ob.text or "").split()[:12]
    query = " ".join(words).strip() or ob.obligation_type or "contract obligation"
    fallback_confidence = min(0.65, max(cfg.routing_ipc_max_confidence + 0.05, 0.61))
    return ObligationRoutingPlan(
        obligation_id=ob.obligation_id,
        intent=query,
        concepts=[ob.obligation_type] if ob.obligation_type else [],
        search_queries=[query],
        explicit_policy_mentions=list(ob.explicit_policy_mentions),
        confidence=fallback_confidence,
        reasoning="planner fallback (cap exceeded or LLM error)",
        routing_source="planner_fallback",
    )


async def plan_obligation_routing(
    obligations: list[ContractObligation],
    *,
    contract_type: str | None,
    catalog_entries: list[CatalogEntry],
    settings: ReviewSettings | None = None,
    tenant_id: str = "",
    catalog_version: str = "",
) -> dict[str, ObligationRoutingPlan]:
    cfg = settings or get_settings()
    if not obligations:
        return {}

    template = _PROMPT_PATH.read_text(encoding="utf-8")
    system_tpl, user_tpl = _split_prompt(template)
    plans: dict[str, ObligationRoutingPlan] = {}

    for ob in obligations:
        if tenant_id and catalog_version:
            key = plan_cache_key(tenant_id=tenant_id, catalog_version=catalog_version, obligation=ob)
            cached = get_cached_plan(key, cfg)
            if cached is not None:
                plans[ob.obligation_id] = cached
    remaining = [ob for ob in obligations if ob.obligation_id not in plans]
    if not remaining:
        return plans

    for start in range(0, len(remaining), cfg.semantic_planner_batch_size):
        batch = remaining[start : start + cfg.semantic_planner_batch_size]
        if (
            cfg.max_planner_calls_per_review > 0
            and planner_calls() >= cfg.max_planner_calls_per_review
        ):
            for ob in batch:
                plans[ob.obligation_id] = _fallback_plan(ob, settings=cfg)
            continue
        increment_planner_calls()
        metrics.record_routing_planner_call()
        try:
            user = user_tpl.format(
                contract_type=contract_type or "unknown",
                policy_titles_block=_policy_titles_block(catalog_entries),
                obligations_block=_obligations_block(batch, cfg.semantic_planner_max_obligation_chars),
            )
            model = get_review_model(
                temperature=cfg.compliance_llm_temperature,
                max_tokens=cfg.compliance_llm_max_tokens,
            )
            result = await invoke_structured(
                model,
                BatchRoutingPlanResult,
                system=system_tpl,
                user=user,
            )
            by_id = {item.obligation_id: item for item in result.plans}
            for ob in batch:
                item = by_id.get(ob.obligation_id)
                if item is None:
                    plans[ob.obligation_id] = _fallback_plan(ob, settings=cfg)
                    continue
                plans[ob.obligation_id] = ObligationRoutingPlan(
                    obligation_id=ob.obligation_id,
                    intent=(item.intent or "").strip(),
                    concepts=[c.strip() for c in item.concepts if str(c).strip()],
                    search_queries=[q.strip() for q in item.search_queries if str(q).strip()],
                    explicit_policy_mentions=list(ob.explicit_policy_mentions),
                    confidence=_apply_planner_confidence_floor(
                        ob,
                        float(item.confidence),
                        cfg,
                    ),
                    reasoning=(item.reasoning or "").strip(),
                    routing_source="llm",
                )
                if tenant_id and catalog_version:
                    key = plan_cache_key(
                        tenant_id=tenant_id,
                        catalog_version=catalog_version,
                        obligation=ob,
                    )
                    set_cached_plan(key, plans[ob.obligation_id], settings=cfg)
        except Exception as exc:  # noqa: BLE001
            logger.warning("semantic routing planner LLM failed for batch: %s", exc)
            for ob in batch:
                plans[ob.obligation_id] = _fallback_plan(ob, settings=cfg)

    return plans
