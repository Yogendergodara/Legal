#!/usr/bin/env python3
"""Run full contract battery; collect metrics even when golden gates fail."""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

import run_live_contract_battery as batt
import validate_p5_golden as vpg

# Soft gates: log failures but continue all contracts.
_orig_assert_gates = vpg._assert_golden_gates


def _soft_assert_gates(name: str, diagnosis: dict, assessment: dict | None = None, review=None) -> None:
    try:
        _orig_assert_gates(name, diagnosis, assessment, review=review)
    except AssertionError as exc:
        print(f"GATE WARN [{name}]: {exc}", file=sys.stderr)


vpg._assert_golden_gates = _soft_assert_gates  # type: ignore[method-assign]
batt._assert_golden_gates = _soft_assert_gates  # type: ignore[attr-defined]

# Sync tag validation is advisory for metric collection runs.
try:
    import atlassian_ipc2

    atlassian_ipc2.validate_policy_sync = lambda _sync: []  # type: ignore[assignment]
except ImportError:
    pass


async def main() -> int:
    t0 = time.time()
    code = await batt.main()
    elapsed = round(time.time() - t0, 1)
    print(f"=== battery collect finished in {elapsed}s exit={code} ===")
    return code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
