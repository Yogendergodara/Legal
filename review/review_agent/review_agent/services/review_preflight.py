"""Fail-fast dependency checks before review graph execution."""

from __future__ import annotations

import os

from document_core.schemas.taxonomy import normalize_categories
from review_agent.clients.document_client import DocumentMCPClient, STALE_MCP_PROBE_MESSAGE
from review_agent.config import ReviewSettings, get_settings

STALE_MCP_MESSAGE = STALE_MCP_PROBE_MESSAGE


class ReviewPreflightError(RuntimeError):
    """Review cannot start — dependency unavailable."""


def _env(name: str) -> str:
    value = os.environ.get(name)
    return value if value not in (None, "") else ""


def check_llm_credentials() -> None:
    from review_agent.models.llm_key_pool import current_api_key, pool_active

    if pool_active():
        if current_api_key():
            return
    api_key = (
        _env("REVIEW_LLM_API_KEY")
        or _env("LLM_API_KEY")
        or _env("OPENAI_API_KEY")
        or _env("MISTRAL_API_KEY")
    )
    if api_key or _env("LLM_BASE_URL"):
        return
    raise ReviewPreflightError("LLM credentials not configured")


async def check_document_mcp(client: DocumentMCPClient) -> None:
    data = await client.health()
    if data.get("status") != "ok":
        raise ReviewPreflightError(f"document-mcp unhealthy: {data}")
    if data.get("db") != "ok":
        raise ReviewPreflightError("document-mcp Postgres ping failed")


async def check_mcp_search_metadata_capability(
    client: DocumentMCPClient,
    *,
    tenant_id: str = "e2e-demo",
) -> None:
    """Probe P0-1 surface: search_policy_by_categories with metadata.categories."""
    try:
        await client.probe_search_metadata_capability(tenant_id=tenant_id)
    except RuntimeError as exc:
        raise ReviewPreflightError(str(exc)) from exc


async def check_scoped_documents_indexed(
    client: DocumentMCPClient,
    *,
    tenant_id: str,
    policy_document_ids: list[str],
    contract_document_id: str | None = None,
) -> list[str]:
    """Verify scoped IDs exist and are indexed; return non-fatal warnings."""
    response = await client.list_policy_registry(tenant_id)
    by_id = {str(record.document_id): record for record in response.policies}
    warnings: list[str] = []

    def _check_doc(doc_id: str, label: str) -> None:
        record = by_id.get(doc_id)
        if record is None:
            raise ReviewPreflightError(f"{label} document not found: {doc_id}")
        if record.index_status != "indexed":
            raise ReviewPreflightError(
                f"{label} {doc_id} index_status={record.index_status}; expected indexed"
            )

    if contract_document_id:
        _check_doc(str(contract_document_id), "contract")

    for raw_id in policy_document_ids:
        pid = str(raw_id).strip()
        if not pid:
            continue
        _check_doc(pid, "policy")
        cats = normalize_categories((by_id[pid].metadata or {}).get("categories"))
        if not cats or cats == ["general"]:
            warnings.append(
                f"policy {pid} has only general categories; retrieval may be weak"
            )

    return warnings


async def run_review_preflight(
    client: DocumentMCPClient,
    *,
    preflight_enabled: bool = True,
    mcp_capability_probe: bool | None = None,
    tenant_id: str | None = None,
    policy_document_ids: list[str] | None = None,
    contract_document_id: str | None = None,
    settings: ReviewSettings | None = None,
    reviewable_sections: int | None = None,
) -> list[str]:
    if not preflight_enabled:
        return []
    cfg = settings or get_settings()
    check_llm_credentials()
    await check_document_mcp(client)
    probe = (
        mcp_capability_probe
        if mcp_capability_probe is not None
        else cfg.review_preflight_mcp_capability_probe
    )
    if probe and tenant_id:
        await check_mcp_search_metadata_capability(client, tenant_id=tenant_id)
    elif probe:
        await check_mcp_search_metadata_capability(client)
    warnings: list[str] = []
    if tenant_id and policy_document_ids:
        warnings.extend(
            await check_scoped_documents_indexed(
                client,
                tenant_id=tenant_id,
                policy_document_ids=policy_document_ids,
                contract_document_id=contract_document_id,
            )
        )
    from review_agent.services.config_advisory import (
        evaluate_config_advisories,
        format_config_advisory_warnings,
    )

    advisories = evaluate_config_advisories(
        cfg,
        tenant_id=tenant_id or "",
        reviewable_sections=reviewable_sections,
    )
    warnings.extend(format_config_advisory_warnings(advisories))
    return warnings
