#!/usr/bin/env python3

"""Run live P5 contract battery (direct MCP + run_review, bypasses dev UI)."""



from __future__ import annotations



import asyncio

import json

import os

import sys

import time

from pathlib import Path

from typing import Any



from bootstrap_env import apply_golden_review_defaults, load_env, setup_pythonpath



load_env()
os.environ.setdefault("GOLDEN_LLM_PROFILE_FORCE", "true")
apply_golden_review_defaults()
setup_pythonpath()


def _assert_golden_llm_profile() -> None:
    from review_agent.config import get_settings

    settings = get_settings()
    print(
        f"=== golden llm profile={settings.llm_rate_limit_profile} "
        f"concurrency={settings.llm_global_concurrency} ==="
    )
    force = os.environ.get("GOLDEN_LLM_PROFILE_FORCE", "").strip().lower() in ("1", "true", "yes")
    if force and settings.llm_rate_limit_profile != "mistral_conservative":
        raise RuntimeError(
            "GOLDEN_LLM_PROFILE_FORCE set but LLM_RATE_LIMIT_PROFILE is not mistral_conservative"
        )



from export_assessment import build_assessment

from review_agent.clients.document_client import DocumentMCPClient

from review_agent.graph.review_graph import run_review

from review_output import build_review_output_envelope

from review_scope import policy_document_ids_from_sync

from sync_service import sync_policies_only

from validate_p5_golden import (

    CISCO_EXPECTED,

    ROOT,

    _assert_engine_diagnosis,

    _assert_golden_gates,

    _findings_by_section,

)

from e2e_harness import contract_fixture_to_text, policy_fixture_to_sync



OUT = ROOT / "outputs"

RESULTS_PATH = OUT / "live_contract_battery.json"

BATTERY_COOLDOWN_EVENT_THRESHOLD = 8





def _rate_limit_events(review: dict[str, Any]) -> int:

    meta = (review.get("artifacts") or {}).get("report", {}).get("metadata") or {}

    diagnosis = (

        review.get("engine_diagnosis")

        or meta.get("engine_diagnosis")

        or (review.get("artifact") or {}).get("engine_diagnosis")

        or {}

    )

    resilience = diagnosis.get("resilience") or {}

    return int(resilience.get("llm_rate_limit_events") or 0)





async def _cooldown_after_review(review: dict[str, Any], label: str) -> None:

    events = _rate_limit_events(review)

    if events < BATTERY_COOLDOWN_EVENT_THRESHOLD:

        return

    seconds = min(120.0, float(events) * 2.0)

    print(

        f"=== cooldown after {label}: {events} rate_limit_events, "

        f"sleeping {seconds:.0f}s ==="

    )

    await asyncio.sleep(seconds)





async def _run_direct_review(

    client: DocumentMCPClient,

    *,

    tenant: str,

    contract_text: str,

    contract_title: str,

    contract_type: str,

    query: str,

    policy_document_ids: list[str] | None = None,

) -> dict[str, Any]:

    started = time.time()

    review_kwargs: dict[str, Any] = {

        "client": client,

        "tenant_id": tenant,

        "contract_text": contract_text,

        "contract_title": contract_title,

        "contract_type": contract_type,

    }

    if policy_document_ids:

        review_kwargs["policy_document_ids"] = policy_document_ids

        review_kwargs["policy_scope"] = "request"

    else:

        review_kwargs["policy_scope"] = "indexed"

    state = await run_review(**review_kwargs)

    elapsed = round(time.time() - started, 1)

    report = state.get("report")

    if report is None:

        raise RuntimeError(f"no report: {state.get('warnings')}")

    envelope = build_review_output_envelope(

        report=report,

        state=state,

        contract_document_id=state.get("contract_document_id"),

    )

    review = envelope if isinstance(envelope, dict) else envelope.model_dump(mode="json")

    review["elapsed_seconds"] = elapsed

    return review





async def _run_named_direct(

    client: DocumentMCPClient,

    *,

    name: str,

    fixture_path: Path,

    contract_path: Path,

    contract_title: str,

    contract_type: str,

    query: str,

    tenant: str,

    min_violations: int,

    min_weighted: float | None = None,

    validate_sync: bool = False,

) -> dict[str, Any]:

    data = json.loads(fixture_path.read_text(encoding="utf-8"))

    sync_result = await sync_policies_only(

        client,

        tenant_id=tenant,

        policies=[policy_fixture_to_sync(p) for p in data["policies"]],

        replace_policies=True,

    )

    if validate_sync:

        from atlassian_ipc2 import validate_policy_sync



        sync_errors = validate_policy_sync(sync_result)

        if sync_errors:

            print(f"Sync validation failed for {name}:", file=sys.stderr)

            for err in sync_errors:

                print(f"  {err}", file=sys.stderr)

            raise RuntimeError(f"{name}: policy sync validation failed")

    policy_ids = policy_document_ids_from_sync(sync_result)

    review = await _run_direct_review(

        client,

        tenant=tenant,

        contract_text=contract_path.read_text(encoding="utf-8"),

        contract_title=contract_title,

        contract_type=contract_type,

        query=query,

        policy_document_ids=policy_ids,

    )

    diagnosis = _assert_engine_diagnosis(review, name)

    assessment = build_assessment(review, test_type=f"live_{name}", label=contract_title)

    _assert_golden_gates(name, diagnosis, assessment, review=review)

    violations = assessment["violation_count"]

    weighted = assessment["scores"]["weighted_alignment_score"]

    gate_pass = violations >= min_violations

    if min_weighted is not None and weighted < min_weighted - 5:

        gate_pass = False

    out_path = OUT / f"{name}_review_live.json"

    out_path.write_text(json.dumps(review, indent=2, ensure_ascii=False), encoding="utf-8")

    return {

        "test": name,

        "elapsed_seconds": review.get("elapsed_seconds"),

        "violation_count": violations,

        "weighted_alignment_score": weighted,

        "pipeline_mode": diagnosis.get("pipeline_mode"),

        "gate_pass": gate_pass,

        "gate_min_violations": min_violations,

    }





async def main() -> int:

    OUT.mkdir(exist_ok=True)

    results: list[dict[str, Any]] = []

    _assert_golden_llm_profile()

    skip_cisco = os.environ.get("BATTERY_SKIP_CISCO", "").strip().lower() in ("1", "true", "yes")

    async with DocumentMCPClient.open("http://127.0.0.1:8003") as client:

        if not skip_cisco:
            print("=== Cisco ===")
            tenant = "cisco-beta"
            contract = json.loads(
                (ROOT / "fixtures" / "cisco" / "acme_hardware_supplier_agreement.json").read_text()
            )
            policies = [
                json.loads(p.read_text(encoding="utf-8"))
                for p in sorted((ROOT / "fixtures" / "cisco" / "policies").glob("*.json"))
            ]
            await sync_policies_only(
                client,
                tenant_id=tenant,
                policies=[policy_fixture_to_sync(p) for p in policies],
                replace_policies=True,
            )
            t0 = time.time()
            review = await _run_direct_review(
                client,
                tenant=tenant,
                contract_text=contract_fixture_to_text(contract),
                contract_title=contract.get("title") or "Acme Hardware Supply Agreement",
                contract_type=contract.get("contract_type") or "vendor",
                query="Review this hardware supplier agreement against Cisco supplier policies",
            )
            diagnosis = _assert_engine_diagnosis(review, "cisco")
            assessment = build_assessment(review, test_type="live_cisco", label="Cisco")
            _assert_golden_gates("cisco", diagnosis, assessment, review=review)
            from beta_test.benchmark_score import score_section_expected, specs_from_legacy_expected

            by_section = _findings_by_section(review)
            specs = specs_from_legacy_expected(CISCO_EXPECTED)
            _, _, score = score_section_expected(by_section, specs)
            min_score = float(
                json.loads((ROOT / "golden_thresholds.json").read_text(encoding="utf-8"))
                .get("cisco", {})
                .get("min_legal_score_10", 6.0)
            )
            cisco_result = {
                "test": "cisco",
                "elapsed_seconds": round(time.time() - t0, 1),
                "legal_score_10": score,
                "violation_count": assessment["violation_count"],
                "pipeline_mode": diagnosis.get("pipeline_mode"),
                "gate_pass": score >= min_score,
                "gate_min_score": min_score,
            }
            (OUT / "cisco_review_live.json").write_text(
                json.dumps(review, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            results.append(cisco_result)
            print(cisco_result)
            await _cooldown_after_review(review, "cisco")

        for spec in (

            (

                "atlassian",

                ROOT / "fixtures" / "atlassian_e2e.json",

                ROOT / "fixtures" / "atlassian_customer_agreement.txt",

                "Atlassian Customer Agreement",

                "saas",

                "Review this Atlassian Customer Agreement against all indexed Atlassian policies",

                6,

                None,

                True,

            ),

            (

                "ula",

                ROOT / "fixtures" / "xecurify_e2e.json",

                ROOT / "fixtures" / "xecurify_ula_contract.txt",

                "User License Agreement - Xecurify / Customer",

                "saas",

                "Review this Xecurify user license agreement against our security, privacy, data retention, incident response, and code of conduct policies",

                2,

                None,

                False,

            ),

            (

                "eula",

                ROOT / "fixtures" / "xecurify_e2e.json",

                ROOT / "fixtures" / "xecurify_plugin_eula.txt",

                "End User License Agreement - Xecurify Plugin",

                "saas",

                "Review this Xecurify plugin end user license agreement against our privacy policy, security practices, data retention, incident response, and code of conduct policies",

                3,

                39.2,

                False,

            ),

        ):

            name, fixture, contract_path, title, ctype, query, min_v, min_w, validate_sync = spec

            fixture_data = json.loads(fixture.read_text(encoding="utf-8"))

            tenant = fixture_data.get("tenant_id", "e2e-demo")

            print(f"=== {name} (tenant={tenant}) ===")

            row = await _run_named_direct(

                client,

                name=name,

                fixture_path=fixture,

                contract_path=contract_path,

                contract_title=title,

                contract_type=ctype,

                query=query,

                tenant=tenant,

                min_violations=min_v,

                min_weighted=min_w,

                validate_sync=validate_sync,

            )

            results.append(row)

            print(row)

            review_path = OUT / f"{name}_review_live.json"

            if review_path.is_file():

                review_payload = json.loads(review_path.read_text(encoding="utf-8"))

                await _cooldown_after_review(review_payload, name)



        print("=== nda ===")

        nda_data = json.loads((ROOT / "fixtures" / "xecurify_e2e.json").read_text(encoding="utf-8"))

        tenant = nda_data.get("tenant_id", "xecurify-demo")

        nda_sync = await sync_policies_only(

            client,

            tenant_id=tenant,

            policies=[policy_fixture_to_sync(p) for p in nda_data["policies"]],

            replace_policies=True,

        )

        nda_policy_ids = policy_document_ids_from_sync(nda_sync)

        nda_review = await _run_direct_review(

            client,

            tenant=tenant,

            contract_text=nda_data["contract_text"],

            contract_title="Mutual NDA - Xecurify / Recipient",

            contract_type="nda",

            query="Review this mutual NDA against our Code of Conduct, data retention, security, and privacy policies",

            policy_document_ids=nda_policy_ids,

        )

        nda_diag = _assert_engine_diagnosis(nda_review, "nda")

        nda_assess = build_assessment(nda_review, test_type="live_nda", label="Mutual NDA")

        _assert_golden_gates("nda", nda_diag, nda_assess, review=nda_review)

        nda_v = nda_assess["violation_count"]

        nda_row = {

            "test": "nda",

            "elapsed_seconds": nda_review.get("elapsed_seconds"),

            "violation_count": nda_v,

            "pipeline_mode": nda_diag.get("pipeline_mode"),

            "gate_pass": nda_v >= 1,

            "gate_min_violations": 1,

        }

        (OUT / "nda_review_live.json").write_text(json.dumps(nda_review, indent=2, ensure_ascii=False), encoding="utf-8")

        results.append(nda_row)

        print(nda_row)

        await _cooldown_after_review(nda_review, "nda")



    RESULTS_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")

    print(f"Wrote {RESULTS_PATH}")

    failed = [r["test"] for r in results if not r.get("gate_pass")]

    if failed:

        print("GATE FAIL:", ", ".join(failed))

        return 1

    return 0





if __name__ == "__main__":

    raise SystemExit(asyncio.run(main()))


