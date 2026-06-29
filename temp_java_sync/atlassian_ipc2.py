"""IPC-2 helpers — Atlassian policy sync validation and index checks."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

ATLASSIAN_POLICY_REFS: tuple[str, ...] = (
    "atlassian-privacy-policy",
    "atlassian-copyright-trademark",
    "atlassian-third-party-code-policy",
    "atlassian-advisory-services-policy",
    "atlassian-acceptable-use-policy",
    "atlassian-government-amendment",
    "atlassian-data-processing-addendum",
    "atlassian-product-specific-terms",
    "atlassian-ai-terms",
)

ATLASSIAN_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "atlassian_e2e.json"
SYNC_OUT = Path(__file__).resolve().parent / "outputs" / "sync_atlassian_e2e-demo.json"


def validate_policy_sync(sync: dict[str, Any], *, expected_count: int = 9) -> list[str]:
    """Return human-readable errors; empty list means pass."""
    errors: list[str] = []
    policies = sync.get("policies") or []
    if len(policies) < expected_count:
        errors.append(f"expected>={expected_count} policies synced, got {len(policies)}")

    preflight = sync.get("preflight") or {}
    weak_count = int(preflight.get("weak_tag_count") or 0)
    if weak_count:
        errors.append(f"weak_tag_count={weak_count} policies={preflight.get('weak_tag_policies')}")

    for policy in policies:
        ref = policy.get("policy_ref") or policy.get("title") or "?"
        tagger = policy.get("tagger")
        if tagger != "llm":
            errors.append(f"{ref}: tagger={tagger!r} (expected llm)")
        warnings = policy.get("warnings") or []
        for warning in warnings:
            if "tagger=keyword" in warning or "weak_tags" in warning:
                errors.append(f"{ref}: {warning}")

    return errors


def missing_atlassian_refs(
    tenant_id: str = "e2e-demo",
    *,
    database_url: str | None = None,
) -> list[str]:
    """Return policy_ref values from ATLASSIAN_POLICY_REFS not indexed for tenant."""
    import psycopg2

    url = database_url or os.environ.get(
        "DATABASE_URL",
        "postgresql://legalai:legalai@127.0.0.1:5435/legalai",
    )
    conn = psycopg2.connect(url)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT policy_ref
            FROM policy_documents
            WHERE tenant_id = %s AND kind = 'policy' AND index_status = 'indexed'
            """,
            (tenant_id,),
        )
        have = {row[0] for row in cur.fetchall()}
    finally:
        conn.close()
    return [ref for ref in ATLASSIAN_POLICY_REFS if ref not in have]
