#!/usr/bin/env python3
"""Review only — prod path via contract_document_id (after sync)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from bootstrap_env import apply_golden_review_defaults, load_env, setup_pythonpath

load_env()
apply_golden_review_defaults()
setup_pythonpath()

from review_agent.clients.document_client import DocumentMCPClient  # noqa: E402
from review_agent.config import get_settings  # noqa: E402
from review_agent.graph.review_graph import run_review  # noqa: E402
from review_output import build_review_output_envelope  # noqa: E402
from review_scope import policy_document_ids_from_sync  # noqa: E402


async def main() -> int:
    import os

    root = load_env()
    sync_path = root / "outputs" / "sync_result.json"
    if not sync_path.is_file():
        print("ERROR: run run_sync_only.py first (missing outputs/sync_result.json)", file=sys.stderr)
        return 1

    sync = json.loads(sync_path.read_text(encoding="utf-8"))
    contract = sync["contract"]
    policy_document_ids = policy_document_ids_from_sync(sync)
    if not policy_document_ids:
        print("ERROR: no policy document IDs in sync_result.json", file=sys.stderr)
        return 1
    tenant = os.environ.get("E2E_TENANT_ID", "e2e-demo")
    base_url = os.environ.get("DOCUMENT_SERVER_URL", "http://localhost:8003")

    get_settings.cache_clear()
    settings = get_settings()
    if not os.environ.get("LLM_API_KEY") and not os.environ.get("MISTRAL_API_KEY"):
        print("ERROR: set LLM_API_KEY in temp_java_sync/.env for real review", file=sys.stderr)
        return 1

    client = DocumentMCPClient(base_url)
    state = await run_review(
        client=client,
        tenant_id=tenant,
        contract_document_id=contract["document_id"],
        contract_title="Mutual NDA (E2E)",
        contract_type="nda",
        policy_document_ids=policy_document_ids,
    )

    report = state.get("report")
    if report is None:
        print("ERROR: no report produced", file=sys.stderr)
        print("warnings:", state.get("warnings"), file=sys.stderr)
        return 1

    out_dir = root / "outputs"
    out_dir.mkdir(exist_ok=True)
    payload = build_review_output_envelope(
        report=report,
        state=state,
        contract_document_id=contract["document_id"],
    )
    (out_dir / "review_result.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"Findings: {payload['finding_count']}")
    print(f"Pipeline: {report.metadata.get('pipeline')}")
    artifact = report.metadata.get("artifact") or {}
    ops = artifact.get("ops") or {}
    print(f"Ungrounded: {ops.get('ungrounded_count', '?')} | Grounding downgraded: {ops.get('grounding_downgraded_count', '?')}")
    print(f"\nWrote {out_dir / 'review_result.json'}")
    print("\n--- Summary (first 1200 chars) ---\n")
    print((report.summary_markdown or "")[:1200])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
