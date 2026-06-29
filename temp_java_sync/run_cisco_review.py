#!/usr/bin/env python3
"""Standalone Cisco golden review (RC-11)."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from bootstrap_env import apply_golden_review_defaults, load_env, setup_pythonpath

load_env()
os.environ.setdefault("GOLDEN_LLM_PROFILE_FORCE", "true")
apply_golden_review_defaults()
setup_pythonpath()

from beta_test.benchmark_score import score_section_expected, specs_from_legacy_expected
from e2e_harness import contract_fixture_to_text, policy_fixture_to_sync
from export_assessment import build_assessment
from review_agent.clients.document_client import DocumentMCPClient
from review_agent.graph.review_graph import run_review
from review_output import build_review_output_envelope
from sync_service import sync_policies_only
from validate_p5_golden import (
    CISCO_EXPECTED,
    ROOT,
    _assert_engine_diagnosis,
    _assert_golden_gates,
    _findings_by_section,
)

OUT = ROOT / "outputs"
BASELINE_PROFILE = "cisco_v1"


async def main() -> int:
    tenant = "cisco-beta"
    contract_path = ROOT / "fixtures" / "cisco" / "acme_hardware_supplier_agreement.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    policies = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted((ROOT / "fixtures" / "cisco" / "policies").glob("*.json"))
    ]
    os.environ.setdefault("BASELINE_PROFILE", BASELINE_PROFILE)

    async with DocumentMCPClient.open("http://127.0.0.1:8003") as client:
        await sync_policies_only(
            client,
            tenant_id=tenant,
            policies=[policy_fixture_to_sync(p) for p in policies],
            replace_policies=True,
        )
        t0 = time.time()
        report = await run_review(
            client,
            tenant_id=tenant,
            contract_text=contract_fixture_to_text(contract),
            contract_title=contract.get("title") or "Acme Hardware Supply Agreement",
            contract_type=contract.get("contract_type") or "vendor",
            query="Review this hardware supplier agreement against Cisco supplier policies",
        )
        elapsed = round(time.time() - t0, 1)
        review = build_review_output_envelope(report, elapsed_seconds=elapsed)

    diagnosis = _assert_engine_diagnosis(review, "cisco")
    assessment = build_assessment(review, test_type="live_cisco", label="Cisco")
    _assert_golden_gates("cisco", diagnosis, assessment, review=review)

    by_section = _findings_by_section(review)
    specs = specs_from_legacy_expected(CISCO_EXPECTED)
    hits, section_results, score = score_section_expected(by_section, specs)
    print(f"legal_score_10={score} hits={hits}/{len(specs)} elapsed={elapsed}s")

    OUT.mkdir(exist_ok=True)
    (OUT / "cisco_review_live.json").write_text(
        json.dumps(review, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (OUT / "cisco_assessment_live.json").write_text(
        json.dumps({**assessment, "legal_score_10": score, "section_results": section_results}, indent=2),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
