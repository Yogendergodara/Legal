"""Load temp_java_sync/.env into os.environ before review runs."""

from __future__ import annotations

import os
from pathlib import Path

_REVIEW_ENV_PREFIXES = (
    "DISCOVERY_",
    "SECTION_",
    "COMPARE_",
    "RETRIEVAL_",
    "GUARD_",
    "LLM_",
    "GAP_",
    "FINAL_",
    "REVIEW_",
    "ENFORCE_",
    "FINDING_",
    "PLAYBOOK_",
    "GROUNDING_",
    "RERANKER_",
    "OBLIGATION_",
    "EVIDENCE_",
    "ROUTING_",
    "CATALOG_",
    "MAX_OBLIGATIONS_",
    "MAX_PLANNER_",
    "MAX_CATALOG_",
)


def _should_load_review_env_key(key: str) -> bool:
    if key in {"MISTRAL_API_KEY"}:
        return True
    return any(key.startswith(prefix) for prefix in _REVIEW_ENV_PREFIXES)


def load_env(*, dev_ui: bool = False) -> Path:
    """Load temp_java_sync/.env. Dev UI uses temp_java only (no review_agent merge)."""
    root = Path(__file__).resolve().parent
    env_path = root / ".env"
    example = root / ".env.example"
    target = env_path if env_path.is_file() else example
    if not target.is_file():
        return root

    for line in target.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if dev_ui:
            os.environ[key] = value
        elif value:
            os.environ.setdefault(key, value)

    if dev_ui:
        # Pydantic Settings also reads review_agent/.env — clear pool keys so LLM_API_KEY wins.
        if os.environ.get("LLM_KEY_POOL_ENABLED", "").lower() not in ("1", "true", "yes"):
            os.environ["LLM_API_KEYS"] = ""
            os.environ["LLM_KEY_POOL_ENABLED"] = "false"
        return root

    review_env = root.parent / "review" / "review_agent" / ".env"
    review_example = root.parent / "review" / "review_agent" / ".env.example"
    for source in (review_env, review_example):
        if not source.is_file():
            continue
        for line in source.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            key = key.strip()
            value = value.strip()
            if not key or not _should_load_review_env_key(key) or not value:
                continue
            if not os.environ.get(key):
                os.environ[key] = value
    return root


def apply_golden_tenant_rollout_defaults() -> None:
    """Enable obligation routing + parallel hybrid for all tenants (empty allowlist = global)."""
    os.environ["OBLIGATION_ROUTING_ENABLED"] = "true"
    os.environ["REVIEW_PIPELINE_MODE"] = "parallel_hybrid"
    # Override review_agent/.env pilot allowlist (e.g. e2e-demo) so battery tenants get routing.
    os.environ["OBLIGATION_ROUTING_TENANT_ALLOWLIST"] = ""


def apply_golden_llm_profile_defaults() -> None:
    """RC-12 — battery/golden runs use conservative Mistral pacing unless opted out."""
    if os.environ.get("GOLDEN_LLM_PROFILE_OPT_OUT", "").strip().lower() in ("1", "true", "yes"):
        return
    force = os.environ.get("GOLDEN_LLM_PROFILE_FORCE", "").strip().lower() in ("1", "true", "yes")
    if force:
        os.environ["LLM_RATE_LIMIT_PROFILE"] = "mistral_conservative"
    else:
        os.environ.setdefault("LLM_RATE_LIMIT_PROFILE", "mistral_conservative")


def apply_golden_review_defaults() -> None:
    """RC-05 — P5-aligned obligation cap when not explicitly configured."""
    apply_golden_tenant_rollout_defaults()
    apply_golden_llm_profile_defaults()
    apply_sr01_retrieval_defaults()
    apply_ob_ipc_recovery_defaults()
    apply_pr01_precision_defaults()
    if os.environ.get("MAX_OBLIGATIONS_PER_REVIEW", "").strip() == "":
        os.environ["MAX_OBLIGATIONS_PER_REVIEW"] = "80"


def apply_ob_ipc_recovery_defaults() -> None:
    """OB-01/04 — non-429 IPC recovery defaults for golden/A/B (opt out: OB_IPC_RECOVERY_OPT_OUT=true)."""
    if os.environ.get("OB_IPC_RECOVERY_OPT_OUT", "").strip().lower() in ("1", "true", "yes"):
        return
    os.environ.setdefault("OBLIGATION_RETRIEVAL_SKIP_RESOLVED_SECTIONS", "false")
    os.environ.setdefault("OBLIGATION_SKIP_RESOLVED_PARALLEL_GUARD", "true")
    os.environ.setdefault("EVIDENCE_MIN_CONCEPT_OVERLAP", "0.15")
    os.environ.setdefault("ROUTING_COMPARE_MIN_CONFIDENCE", "0.75")


def apply_pr01_precision_defaults() -> None:
    """PR-01 — precision funnel recovery (opt out: PR01_PRECISION_OPT_OUT=true)."""
    if os.environ.get("PR01_PRECISION_OPT_OUT", "").strip().lower() in ("1", "true", "yes"):
        return
    os.environ.setdefault("EVIDENCE_RERANK_BYPASS_ENABLED", "true")
    os.environ.setdefault("EVIDENCE_RERANK_BYPASS_MIN_CONFIDENCE", "0.55")
    os.environ.setdefault("EVIDENCE_EXPAND_MAX_ROUNDS", "2")
    os.environ.setdefault("EVIDENCE_EXPAND_BROADEN_MODE", "both")
    os.environ.setdefault("EVIDENCE_EXPAND_MAX_EXTRA_DOCS", "3")
    os.environ.setdefault("CATALOG_MATCH_TOP_K", "12")
    os.environ.setdefault("CATALOG_MATCH_MAX_CANDIDATES", "8")
    os.environ.setdefault("MAX_CATALOG_SEARCH_CALLS_PER_REVIEW", "150")
    os.environ.setdefault("OBLIGATION_RETRIEVAL_UNION_TOP_K", "20")
    os.environ.setdefault("OBLIGATION_RETRIEVAL_MAX_QUERIES", "4")
    os.environ.setdefault("OBLIGATION_COMPARE_MAX_OBLIGATION_CHARS", "3000")
    os.environ.setdefault("PLAYBOOK_COMPARE_MAX_CHARS", "2000")
    os.environ.setdefault("COMPARE_MAX_POLICY_HITS", "3")
    os.environ.setdefault("ROUTING_PLANNER_EXPLICIT_MENTION_CONFIDENCE_FLOOR", "0.55")


def apply_sr01_retrieval_defaults() -> None:
    """SR-01 — meaning-first retrieval for golden/A/B runs (opt out: SR01_RETRIEVAL_OPT_OUT=true)."""
    if os.environ.get("SR01_RETRIEVAL_OPT_OUT", "").strip().lower() in ("1", "true", "yes"):
        return
    os.environ.setdefault("RETRIEVAL_MEANING_FIRST_ENABLED", "true")
    os.environ.setdefault("RETRIEVAL_CATEGORY_HARD_FILTER", "false")
    os.environ.setdefault("COMPARE_HIT_ALLOW_PRIMARY_FALLBACK", "true")


def setup_pythonpath() -> None:
    import sys

    legal = Path(__file__).resolve().parent.parent
    paths = [
        str(legal / "document_core"),
        str(legal / "review" / "review_agent"),
        str(legal / "Legal ai"),
        str(legal / "temp_java_sync"),
    ]
    existing = os.environ.get("PYTHONPATH", "")
    parts = [p for p in paths + ([existing] if existing else []) if p]
    os.environ["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(parts))
    for path in paths:
        if path not in sys.path:
            sys.path.insert(0, path)
