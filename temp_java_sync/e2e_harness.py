#!/usr/bin/env python3
"""Shared Dev UI E2E helpers (Phase F3)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from export_assessment import OUTPUTS, export_assessment

DEV_UI_BASE = "http://localhost:8090"


async def sync_policies(
    http: httpx.AsyncClient,
    policies: list[dict[str, Any]],
    *,
    tenant_id: str | None = None,
    tenant_shared: bool = True,
    replace: bool = False,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "policies": policies,
        "use_shared_tenant": tenant_shared,
        "replace_tenant_policies": replace,
    }
    if tenant_id:
        body["tenant_id"] = tenant_id
    response = await http.post(f"{DEV_UI_BASE}/api/sync-policies", json=body)
    response.raise_for_status()
    return response.json()


async def review_text(
    http: httpx.AsyncClient,
    *,
    contract_text: str,
    contract_title: str,
    contract_type: str = "nda",
    query: str = "Review this contract against our policies",
    tenant_id: str | None = None,
    use_platform: bool = False,
    policy_document_ids: list[str] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "query": query,
        "contract_text": contract_text,
        "contract_title": contract_title,
        "contract_type": contract_type,
        "use_platform": use_platform,
    }
    if tenant_id:
        body["tenant_id"] = tenant_id
    if policy_document_ids:
        body["policy_document_ids"] = policy_document_ids
    response = await http.post(f"{DEV_UI_BASE}/api/review-text", json=body)
    response.raise_for_status()
    return response.json()


def policy_fixture_to_sync(policy: dict[str, Any]) -> dict[str, Any]:
    """Convert structured policy fixture to dev-ui sync body entry."""
    sections = policy.get("sections") or []
    if sections:
        text = "\n\n".join(
            f"{s.get('title', '').strip()}\n{s.get('text', '').strip()}".strip()
            for s in sections
            if s.get("text")
        )
    else:
        text = str(policy.get("text") or "").strip()
    return {
        "policy_ref": policy.get("policy_ref"),
        "title": policy.get("title") or policy.get("policy_ref") or "Policy",
        "text": text,
        "policy_type": policy.get("policy_type") or "nda",
    }


def contract_fixture_to_text(contract: dict[str, Any]) -> str:
    sections = contract.get("sections") or []
    if sections:
        return "\n\n".join(
            f"{s.get('section_id', '')}. {s.get('title', '')}\n{s.get('text', '')}".strip()
            for s in sections
        )
    return str(contract.get("contract_text") or contract.get("text") or "").strip()


def _sync_path_for_review(review_path: Path) -> Path | None:
    if not review_path.is_file():
        return None
    review = json.loads(review_path.read_text(encoding="utf-8"))
    tenant = str(review.get("tenant_id") or "").strip()
    if tenant:
        tenant_sync = OUTPUTS / f"sync_{tenant}.json"
        if tenant_sync.is_file():
            return tenant_sync
    fallback = OUTPUTS / "sync_result.json"
    return fallback if fallback.is_file() else None


def export_named_assessment(slug: str) -> Path:
    review_path = OUTPUTS / "review_result.json"
    return export_assessment(
        review_path,
        sync_path=_sync_path_for_review(review_path),
        out_path=OUTPUTS / f"{slug}_assessment.json",
    )
