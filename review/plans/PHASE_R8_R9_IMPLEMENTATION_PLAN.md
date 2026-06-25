# Phase R8 + R9 — Detailed Implementation Plan (minimal code)

**Scope:** Golden routing tests + CI gates (R8) + caching, metrics, pilot rollout (R9).  
**Principle:** **Ship gates on routing accuracy**, not only weighted score. Cache and cap cost **without** changing routing semantics. Pilot one tenant before global default flip.

**Depends on:** R0–R7 (full obligation pipeline + audit).  
**Estimated LOC:** R8 ~350–450 new, ~40 touched · R9 ~300–400 new, ~80 touched.  
**Duration:** R8 3–5 days · R9 ~1 week.

---

## Root cause (why R8 + R9 exist)

R0–R7 fix routing **in code**, but production still fails without **enforcement** and **ops controls**.

| Gap | Root cause | R8 fix | R9 fix |
|-----|------------|--------|--------|
| §10.1 / §10.5 IR regressions return | No CI gate on **wrong-policy compare**; only 4 golden obligations today; unit tests are stage-isolated | Unified `test_routing_golden.py` + `wrong_policy_compare_count == 0` in CI | — |
| Weighted score ~57 masks routing bugs | Benchmark job scores **end report**, not routing decisions | Gate on **routing metrics** first; assessment diff optional second gate | Routing summary in assessment export |
| LLM cost spikes on large contracts | Every review re-loads registry, re-plans every obligation | — | Versioned catalog cache + obligation plan cache |
| Cannot enable globally safely | Single bool `OBLIGATION_ROUTING_ENABLED` | R8 green = safe to pilot | Per-tenant allowlist + caps |
| Ops blind to alias vs planner mix | Counters in `compliance_stats` only; no Prometheus routing labels | R8 exports golden metrics in test harness | `record_routing_*` Prometheus counters |
| Xecurify baseline documents wrong-policy | 3 NON_COMPLIANT tied to **Incident Response** on governing law / notices (section path) | Golden asserts IPC + forbidden IR doc on those obligations | Pilot `e2e-demo` first |

**Baseline (pre-R, section path):** `temp_java_sync/outputs/xecurify_nda_assessment.json` — `weighted_alignment_score: 57`, violations at §10.1 + §10.5 cite **Incident Response Plan** (`29356d10-36dc-5ef8-8cf1-a2948f7c2e28`).

**Production rule:** R8 makes wrong routing **unmergeable**. R9 makes obligation path **affordable and rollout-safe**.

---

## Target state (after R8 + R9)

```text
CI (every PR)
  unit job
    └─ pytest -m "not integration" -m "not benchmark"
    └─ pytest tests/test_routing_golden.py  ← mandatory gate
  integration job (optional nightly)
    └─ xecurify routing integration (Postgres + mocked LLM)

Runtime (pilot tenant)
  OBLIGATION_ROUTING_ENABLED=true
  OBLIGATION_ROUTING_TENANT_ALLOWLIST=e2e-demo
  ROUTING_CACHE_ENABLED=true
  → compliance_stats.routing_summary + Prometheus counters
```

**Do not** flip `OBLIGATION_ROUTING_ENABLED=true` globally until R8 CI green **and** pilot tenant ≥70 weighted alignment.

---

## R8 — Golden tests + CI (3–5 days)

### Goal

One test module proves **end-to-end routing decisions** on fixture obligations without live LLM: alias hit, IPC, candidate fence, forbidden wrong-policy doc, finding status.

### R8.1 — Fixture pack (Xecurify + synthetic)

**Files:**

| File | Purpose |
|------|---------|
| `tests/fixtures/routing_golden.json` | Obligation cases (expand 4 → **22+**) |
| `tests/fixtures/xecurify_policy_catalog.json` | **New** — 5 Xecurify policies: doc_id, title, aliases, catalog_profile snippets |
| `tests/fixtures/synthetic_weird_catalog.json` | **New** — 5 non-standard policy names (R8.3) |

**`xecurify_policy_catalog.json` structure (~80 LOC):**

```json
{
  "tenant_id": "e2e-demo",
  "policies": [
    {
      "document_id": "29356d10-36dc-5ef8-8cf1-a2948f7c2e28",
      "policy_ref": "incident-response",
      "title": "Incident Response Plan",
      "aliases": ["Incident Response", "IR Plan"],
      "topics": ["incident", "breach", "notification"]
    },
    {
      "document_id": "cb031cc8-f40e-58e7-87bb-7a315dc61051",
      "title": "Security Practices Policy",
      "aliases": ["Security Practices Policy"]
    }
  ]
}
```

**Golden case schema (extend existing):**

```json
{
  "obligation_id": "10.1-o0",
  "section_id": "10.1",
  "text": "...",
  "is_boilerplate": true,
  "expected_route_decision": "ipc",
  "expect_retrieval": "skip_ipc",
  "expect_evidence_decision": "ipc",
  "expect_finding_status": "INSUFFICIENT_POLICY_CONTEXT",
  "forbidden_doc_ids": ["29356d10-36dc-5ef8-8cf1-a2948f7c2e28"],
  "forbidden_doc_titles": ["Incident Response"]
}
```

**Cases to add (minimum 22 total):**

| ID | Obligation | Expected |
|----|------------|----------|
| §10.1 governing law | boilerplate | ipc, no IR |
| §10.5 notices | boilerplate | ipc, no IR |
| §2.3-o0 security + explicit alias | alias | 1 candidate = Security Practices |
| §2.3-o1 incident notify | planner | IR or expand; **not** governing law doc |
| §3.1 retention | planner | Data Retention doc in candidates |
| §3.2 secure deletion | planner | Data Retention |
| §5.2 human rights | planner | Code of Conduct |
| §5.5 modern slavery | planner | Code of Conduct |
| §2.1 confidentiality | low conf / ipc or expand | no IR as **only** candidate for boilerplate-like |
| Explicit alias only | alias conf=1.0 | skip planner (mock assert) |
| Synthetic: Cyber Defense Manual | catalog search | incident obligation → manual in candidates |
| Synthetic: empty catalog mention | planner fallback | ipc or expand, not invented doc |
| … | + edge cases | tie alias, empty fence, validation reject |

---

### R8.2 — `routing_golden_harness.py` (~120 LOC)

**File:** `review_agent/services/routing_golden_harness.py` (test-only helper, no graph)

```python
@dataclass
class RoutingGoldenResult:
    obligation_id: str
    plan: ObligationRoutingPlan
    match: CatalogMatchResult
    retrieval_skipped: bool
    evidence: EvidenceSufficiencyResult
    finding_status: str | None
    candidate_doc_ids: list[str]
    forbidden_violations: list[str]

async def run_routing_pipeline_for_obligation(
    case: dict,
    *,
    catalog_entries: list[CatalogEntry],
    client: DocumentMCPClient | None = None,
    settings: ReviewSettings,
    mock_planner: Callable | None = None,
) -> RoutingGoldenResult:
```

**Stages (same order as production, no section path):**

```text
1. Build ContractObligation from case
2. semantic: alias OR mock planner → plan
3. catalog_match (real or mock search_policy_catalog)
4. retrieve_for_obligation (mock retrieve_hybrid_attempt in unit tests)
5. evaluate_evidence_sufficiency
6. ipc_item OR skip compare → finding status from obligation merge path
7. validate_obligation_compare_items + count forbidden doc ids in candidates/findings
```

**Metric function (~25 LOC):**

```python
def wrong_policy_compare_count(results: list[RoutingGoldenResult]) -> int:
    """Increment when forbidden_doc_id appears in candidates OR NON_COMPLIANT finding cites forbidden policy."""
```

This is the **primary CI gate**: `assert wrong_policy_compare_count(results) == 0`.

---

### R8.3 — `test_routing_golden.py` (~200 LOC)

**File:** `tests/test_routing_golden.py`

| Test class | Assert |
|------------|--------|
| `TestGoldenIPC` | §10.1, §10.5 → evidence ipc, finding IPC, zero forbidden docs |
| `TestGoldenAlias` | §2.3-o0 → registry_alias, 1 candidate, Security Practices title |
| `TestGoldenFence` | No `candidate_doc_id` outside catalog fixture allow-set |
| `TestGoldenWrongPolicy` | **`wrong_policy_compare_count == 0`** on full fixture load |
| `TestGoldenSyntheticCatalog` | Weird tenant 5 policies — incident query ranks Cyber Defense Manual |
| `TestRegressionFlagOff` | `OBLIGATION_ROUTING_ENABLED=false` → harness not used in e2e (smoke) |

**Mock strategy (minimal, deterministic):**

- **Planner:** return canned `BatchRoutingPlanResult` from case `mock_planner_response` field when present; else fallback plan.
- **Catalog search:** map query keywords → doc scores from fixture.
- **Retrieval:** return hits only from `candidate_doc_ids` with controlled scores.
- **Compare LLM:** not called for ipc cases; mock one COMPLIANT for §2.3-o0 alias path optional.

**Marker:** `@pytest.mark.routing_golden` — run in unit job always (no Postgres).

---

### R8.4 — CI wiring

**File:** `.github/workflows/review-ci.yml` (~25 LOC touched)

```yaml
  routing-golden:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: Legal/review/review_agent
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install dependencies
        run: |
          pip install -e ../../document_core
          pip install -e .
          pip install pytest pytest-asyncio httpx
      - name: Routing golden gate
        run: |
          python -m pytest tests/test_routing_golden.py -q
          python -c "from tests.test_routing_golden import assert_golden_gate; assert_golden_gate()"
```

**Update unit job** (optional merge): add `tests/test_routing_golden.py` to default pytest or keep separate job for clear failure signal.

**`conftest.py`:** register marker `routing_golden`.

---

### R8.5 — Optional assessment regression (stretch)

**File:** `temp_java_sync/tests/test_xecurify_routing_regression.py` (~80 LOC)

| Check | Baseline |
|-------|----------|
| `wrong_policy_compare_count` from exported assessment | 0 after R6 pilot |
| `weighted_alignment_score` | ≥ 70 (informational; not blocking day 1) |
| §10.1 / §10.5 violation status | not NON_COMPLIANT |

Runs in `benchmark-score` job or nightly — **not** blocking PR until pilot stable.

---

### R8 done when

- [ ] `routing_golden.json` has **≥22** cases
- [ ] `test_routing_golden.py` passes locally and in CI
- [ ] **`wrong_policy_compare_count == 0`** on full golden set
- [ ] CI fails if §10.1 case adds IR to `candidate_doc_ids`
- [ ] `pytest -m "not integration"` still green (acme e2e unchanged with flag off)

---

## R9 — Caching, metrics, rollout (~1 week)

### Goal

Make obligation routing **production-operable**: bounded cost, observable decisions, safe tenant-by-tenant enablement.

### R9.1 — Versioned routing cache

**Problem:** Each review calls `list_policy_registry` + rebuilds `CatalogEntry` list; planner re-runs on identical obligations.

**File:** `review_agent/services/routing_cache.py` (~100 LOC)

```python
@dataclass
class TenantCatalogSnapshot:
    tenant_id: str
    catalog_version: str          # max(profile.catalog_version) or content_hash join
    entries: list[CatalogEntry]
    doc_id_set: set[str]
    loaded_at: float

_catalog_cache: dict[str, TenantCatalogSnapshot] = {}

async def get_catalog_snapshot(
    client: DocumentMCPClient,
    tenant_id: str,
    *,
  settings: ReviewSettings,
) -> TenantCatalogSnapshot:
```

**Invalidation:** `catalog_version` change or `ROUTING_CACHE_TTL_SECONDS` (default 300).  
**Wire:** `catalog_registry.load_catalog_entries` checks cache first when `ROUTING_CACHE_ENABLED=true`.

**Planner cache (optional, ~40 LOC):**

```python
# Key: tenant_id + catalog_version + sha256(obligation.text + obligation_type)
_plan_cache: dict[str, ObligationRoutingPlan] = {}
```

Only cache **LLM planner** results, not alias/boilerplate paths. Cap: `ROUTING_PLAN_CACHE_MAX_ENTRIES=500` LRU evict.

---

### R9.2 — Cost controls

**Config** (`config.py` + `.env.example`):

```env
ROUTING_CACHE_ENABLED=true
ROUTING_CACHE_TTL_SECONDS=300
ROUTING_PLAN_CACHE_MAX_ENTRIES=500

MAX_OBLIGATIONS_PER_REVIEW=80          # 0 = unlimited
MAX_PLANNER_CALLS_PER_REVIEW=40        # cap LLM batches
MAX_CATALOG_SEARCH_CALLS_PER_REVIEW=120
```

**Wire (~30 LOC total):**

| Location | Cap |
|----------|-----|
| `obligation_extract_node` | truncate obligations if `> MAX_OBLIGATIONS_PER_REVIEW` + warning |
| `semantic_route_node` | stop planner batches when `MAX_PLANNER_CALLS` exceeded → fallback plan |
| `catalog_matcher.py` | count search calls per review via context var or state stats |

**No change** to alias fast-path or boilerplate skip (already zero-cost).

---

### R9.3 — Metrics + routing summary

**File:** `observability/metrics.py` (~40 LOC added)

```python
def record_routing_decision(decision: str, source: str) -> None: ...
def record_routing_alias_hit() -> None: ...
def record_wrong_policy_blocked() -> None: ...
```

**Counters (Prometheus when `REVIEW_METRICS_ENABLED=true`):**

| Counter | When |
|---------|------|
| `obligation_routing_alias_hit_total` | alias fast-path |
| `obligation_routing_planner_calls_total` | LLM planner batch |
| `obligation_routing_ipc_total` | evidence ipc |
| `obligation_routing_compare_total` | evidence compare |
| `obligation_wrong_policy_blocked_total` | validation reject |

**`compliance_stats.routing_summary`** (built in `obligation_compare_node` or small helper):

```json
{
  "obligation_count": 24,
  "alias_hit_rate": 0.25,
  "planner_calls": 3,
  "ipc_rate": 0.42,
  "compare_rate": 0.33,
  "wrong_policy_blocked": 0,
  "cache_catalog_hit": true
}
```

**Assessment export:** extend `temp_java_sync` envelope with `routing_summary` when present in `compliance_stats`.

---

### R9.4 — Per-tenant rollout

**Config:**

```env
# Global master (unchanged default)
OBLIGATION_ROUTING_ENABLED=false

# R9: allowlist — only these tenants use obligation path when master true
OBLIGATION_ROUTING_TENANT_ALLOWLIST=e2e-demo

# Or denylist for gradual rollout
# OBLIGATION_ROUTING_TENANT_DENYLIST=
```

**Guard (~20 LOC)** in `obligation_extract_node` or shared `routing_enabled_for_tenant(tenant_id, settings) -> bool`:

```python
def obligation_routing_active(tenant_id: str, settings: ReviewSettings) -> bool:
    if not settings.obligation_routing_enabled:
        return False
    allow = settings.obligation_routing_tenant_allowlist
    if allow and tenant_id not in allow:
        return False
    deny = settings.obligation_routing_tenant_denylist
    if deny and tenant_id in deny:
        return False
    return True
```

Replace bare `settings.obligation_routing_enabled` checks in obligation nodes with `obligation_routing_active()` — **one helper, ~15 call sites**.

---

### R9.5 — Rollout runbook (ops, no code)

| Step | Action |
|------|--------|
| 1 | R0 backfill: all pilot tenant policies have `catalog_profile` + vectors |
| 2 | R8 CI green on `main` |
| 3 | Set `OBLIGATION_ROUTING_ENABLED=true` + `TENANT_ALLOWLIST=e2e-demo` on staging |
| 4 | Re-run Xecurify review; export assessment; verify `wrong_policy_compare_count=0` |
| 5 | Compare `weighted_alignment_score` ≥ 70 |
| 6 | Enable metrics + cache on staging; watch planner call count |
| 7 | Add second tenant to allowlist; repeat |
| 8 | Global default flip only after 2+ tenants stable (post-R9) |

**Docs touch:** `.env.example`, `tests/README.md` (golden gate), optional `plans/PHASE_R9_ROLLOUT.md` one-pager.

---

### R9 done when

- [ ] Catalog cache hit on second review same tenant (unit test)
- [ ] Planner cache skips duplicate obligation text (unit test)
- [ ] `MAX_OBLIGATIONS_PER_REVIEW` enforced with warning
- [ ] Prometheus counters increment when metrics enabled
- [ ] Pilot `e2e-demo` on obligation path with weighted alignment ≥ 70
- [ ] Non-allowlisted tenant unchanged when global flag true

---

## File change matrix

| File | Phase | Change |
|------|-------|--------|
| `tests/fixtures/routing_golden.json` | R8 | expand to 22+ |
| `tests/fixtures/xecurify_policy_catalog.json` | R8 | **new** |
| `tests/fixtures/synthetic_weird_catalog.json` | R8 | **new** |
| `services/routing_golden_harness.py` | R8 | **new** (test helper) |
| `tests/test_routing_golden.py` | R8 | **new** |
| `tests/conftest.py` | R8 | marker `routing_golden` |
| `.github/workflows/review-ci.yml` | R8 | routing-golden job |
| `temp_java_sync/tests/test_xecurify_routing_regression.py` | R8 | optional |
| `services/routing_cache.py` | R9 | **new** |
| `services/routing_tenant.py` | R9 | **new** — `obligation_routing_active()` |
| `services/catalog_registry.py` | R9 | cache hook ~15 LOC |
| `graph/*_nodes.py` (obligation path) | R9 | use tenant helper + caps |
| `observability/metrics.py` | R9 | routing counters |
| `config.py`, `.env.example` | R9 | cache + caps + allowlist |
| `tests/test_routing_cache.py` | R9 | **new** |
| `tests/test_routing_tenant.py` | R9 | **new** |

**Do NOT touch in R8/R9:** compare prompts, `multi_retrieval` semantics, section path logic, `named_policy_routing.py` removal (defer post-rollout).

---

## CI gate summary

| Gate | Job | Blocking |
|------|-----|----------|
| `wrong_policy_compare_count == 0` | `routing-golden` | **Yes** |
| `pytest -m "not integration"` | `unit` | **Yes** |
| Acme e2e (flag off) | `unit` | **Yes** |
| Xecurify weighted ≥ 70 | nightly / manual | No (until stable) |
| Integration Postgres | `integration` | Yes (existing) |

---

## Execution order

### R8 (days 1–5)

| Day | Tasks |
|-----|-------|
| 1 | Expand `routing_golden.json` + `xecurify_policy_catalog.json` |
| 2 | `routing_golden_harness.py` + `wrong_policy_compare_count` |
| 3 | `test_routing_golden.py` IPC + alias + fence |
| 4 | Synthetic catalog cases + full gate assert |
| 5 | CI job + conftest marker + README |

### R9 (days 6–12)

| Day | Tasks |
|-----|-------|
| 6 | `routing_cache.py` + catalog_registry wire |
| 7 | Planner cache + unit tests |
| 8 | Cost caps (obligations, planner calls) |
| 9 | `obligation_routing_active()` + allowlist |
| 10 | Prometheus counters + `routing_summary` |
| 11 | Pilot staging Xecurify run |
| 12 | Docs + second tenant checklist |

---

## What NOT to do

| Anti-pattern | Why |
|--------------|-----|
| Block PR on live LLM golden | Flaky; mock planner in R8 |
| Flip global `OBLIGATION_ROUTING_ENABLED=true` in R9 | Allowlist pilot only |
| Cache compare LLM results | Stale findings risk |
| Redis dependency for R9 cache | In-process TTL sufficient for v1 |
| 100+ golden cases in R8 | Start 22; grow with real failures |
| Remove section path in R8/R9 | Rollout fallback until metrics prove cutover |

---

## Success metrics

| Metric | Baseline | R8 target | R9 pilot target |
|--------|----------|-----------|-----------------|
| `wrong_policy_compare_count` (golden) | 3 (Xecurify section) | **0** | **0** |
| Golden cases | 4 | **≥22** | maintain |
| Weighted alignment (Xecurify) | 57 | — | **≥70** |
| Planner calls per Xecurify review | N/A | — | **≤15** with cache |
| Catalog registry calls per review | 1+ per phase | — | **1** with cache |

---

## Immediate first PR (R8 only, mergeable)

1. Expand fixtures + `xecurify_policy_catalog.json`  
2. `routing_golden_harness.py` + `wrong_policy_compare_count`  
3. `test_routing_golden.py` (IPC + wrong-policy gate)  
4. CI `routing-golden` job  

PR 2 (R9): cache + allowlist + metrics (pilot ops).
