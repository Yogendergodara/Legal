#!/usr/bin/env python3
"""Print resolved PR-01 settings on the contract-test bootstrap path."""

from __future__ import annotations

import json
import os
import sys

from bootstrap_env import apply_golden_review_defaults, load_env, setup_pythonpath

load_env()
os.environ.setdefault("GOLDEN_LLM_PROFILE_FORCE", "true")
apply_golden_review_defaults()
setup_pythonpath()

from review_agent.config import build_runtime_settings_snapshot, get_settings  # noqa: E402

PR01_KEYS = (
    "evidence_rerank_bypass_enabled",
    "evidence_min_concept_overlap",
    "catalog_match_max_candidates",
    "obligation_retrieval_union_top_k",
    "retrieval_meaning_first_enabled",
    "retrieval_category_hard_filter",
    "review_pipeline_mode",
    "obligation_retrieval_skip_resolved_sections",
    "obligation_skip_resolved_parallel_guard",
    "compare_max_policy_hits",
)


def resolved_pr01_settings() -> dict:
    get_settings.cache_clear()
    settings = get_settings()
    snapshot = build_runtime_settings_snapshot(settings)
    out = {k: snapshot.get(k, getattr(settings, k, None)) for k in PR01_KEYS}
    out["catalog_match_top_k"] = settings.catalog_match_top_k
    out["evidence_expand_max_rounds"] = settings.evidence_expand_max_rounds
    out["evidence_expand_broaden_mode"] = settings.evidence_expand_broaden_mode
    out["obligation_retrieval_max_queries"] = settings.obligation_retrieval_max_queries
    out["llm_key_pool_enabled"] = settings.llm_key_pool_enabled
    return out


def main() -> int:
    out = resolved_pr01_settings()
    print(json.dumps(out, indent=2, default=str))

    expected = {
        "evidence_rerank_bypass_enabled": True,
        "catalog_match_top_k": 12,
        "catalog_match_max_candidates": 8,
        "obligation_retrieval_union_top_k": 20,
        "evidence_expand_max_rounds": 2,
        "review_pipeline_mode": "parallel_hybrid",
    }
    bad = [f"{k}={out.get(k)!r} want {v!r}" for k, v in expected.items() if out.get(k) != v]
    if bad:
        print("MISMATCH:", "; ".join(bad), file=sys.stderr)
        return 1
    print("PR-01 env OK for contract runs", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
