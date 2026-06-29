# Phase RC-08/09/10 — Routing Pilot, Section Compare Universe & Extract Structure Recovery

**Version:** 1.0  
**ID:** `DR-PHASE-RC080910`  
**Parent:** [PHASE_RC0304_FUNNEL_TENANT_FIX_PLAN.md](./PHASE_RC0304_FUNNEL_TENANT_FIX_PLAN.md) · [PHASE_RC050607_CAP_IPC_F5_RECOVERY_PLAN.md](./PHASE_RC050607_CAP_IPC_F5_RECOVERY_PLAN.md) · [PHASE_B_RC0102_QUOTA_POSTURE_FIX_PLAN.md](./PHASE_B_RC0102_QUOTA_POSTURE_FIX_PLAN.md) · [PHASE_IPC2_SYNC_INDEX_QUALITY_IMPLEMENTATION_PLAN.md](./PHASE_IPC2_SYNC_INDEX_QUALITY_IMPLEMENTATION_PLAN.md) · [PHASE_IPC3_DISCOVERY_RETRIEVAL_TUNING_PLAN.md](./PHASE_IPC3_DISCOVERY_RETRIEVAL_TUNING_PLAN.md)  
**Targets:** RC-08 (routing pilot on polluted tenant) · RC-09 (section compare ran, wrong NC outcome) · RC-10 (obligation extract structure failures)  
**Status:** **IMPLEMENTED**  
**Scope:** Restore P5 section NC accuracy and extract quality without lowering F4 gates, **without batch-size reduction**, non-deterministic semantic routing preserved, wall time **not increased** (target: ↓ wasted planner/compare work on wrong catalog; ↑ structure recovery without extra quota amplification)  
**Effort:** ~0.5 day (RC-08 harness + advisories) + ~0.5 day (RC-09 hit scope + flags) + ~0.25 day (RC-10 structure recovery) + one Atlassian golden re-run  
**Risk:** Low (RC-08/10 config + guards); Medium (RC-09 hit filter — bounded to request-scoped reviews)

**Prerequisite:** [RC-0304](./PHASE_RC0304_FUNNEL_TENANT_FIX_PLAN.md) + [RC-050607](./PHASE_RC050607_CAP_IPC_F5_RECOVERY_PLAN.md) deployed. RC-08/09/10 are **residual** failures visible even when section compare runs.

---

## 1. Problem statement

Live and P5-rerun evidence show three **orthogonal** failure modes after tenant isolation and cap fixes. None is fixed by lowering batch sizes or forcing deterministic routing.

| RC | Symptom | Misread |
|----|---------|---------|
| **RC-08** | `config_advisory: E2b` on `e2e-demo`; routing enabled but `review_pipeline_mode=serial`; P5 rerun with `parallel_hybrid` + 26 policies → `compare_queued=0` | “Routing is the root cause of NC loss” |
| **RC-09** | `compare_items=28`, `llm_batches=7` — section path ran; sections **15, 19, 20.4** still IPC (P5: NC with 9 policies) | “Section compare didn’t run” |
| **RC-10** | All hybrid runs: `obligation extract LLM failed: Expecting value: line 1 column 1` | “Extract still got 117 obligations — extract is fine” |

### Evidence tables

**RC-08 — routing on polluted / shared tenant**

| Run | tenant | routing | pipeline | policies_discovered | compare_queued | NC |
|-----|--------|---------|----------|---------------------|----------------|-----|
| ATL P5 (good) | clean / request scope | off (baseline) | serial | 9 | 34+ | 6 |
| ATL LIVE | `e2e-demo` (pre-0304) | E2b allowlisted | serial | 29 | 0 | 1 |
| ATL P5 rerun | polluted index | on | parallel_hybrid | 26 | 0 | 4 |
| ATL LIVE (post-0304 target) | `atlassian-demo` | should be on | parallel_hybrid (allowlisted) | 9 | >0 | ≥6 |

**RC-09 — compare ran, wrong universe**

| Section | P5 (9 policies) | LIVE (29 policies) | Mechanism |
|---------|-----------------|---------------------|-----------|
| 15 indemnity | NC | IPC | Wrong playbook / policy family in hits |
| 19 publicity | NC | IPC | Misaligned retrieval from mixed catalog |
| 20.4 governing law | NC | IPC | Category-aligned pick from wrong doc |
| 13 | IPC | NC | Inverse — wrong universe can flip either way |

**RC-10 — extract structure failures**

| Signal | Value | Effect |
|--------|-------|--------|
| Warning | `Expecting value: line 1 column 1 (char 0)` | Empty/malformed JSON post-429 |
| `FailureClass` | `STRUCTURE` | Batch fails; single retry may be suppressed in HOT |
| Obligations extracted | 117 (uncapped LIVE) / 80 (P5) | Quantity OK; **quality degraded** (fallback spans, weak types) |
| P5 baseline | `routing_planner_calls=0` | Clean extract → stable downstream routing |

**Causal chain (ordered):**

```text
RC-04 pollution (0304 fixes tenant) ──┬── RC-08 routing/planner amplifies wrong catalog
                                    └── RC-09 section hits from wrong policy universe → IPC masks NC
RC-10 structure failures (often post-429) ── degraded obligation text/types ── planner noise
B-RC HOT posture ── suppresses batch→single structure recovery (RC-10)
```

**Not caused by:** Section compare batch size (7 batches ran). Routing alone on **clean** tenant (P5: 6 NC with routing path available).

---

## 2. Code-proven root causes

### RC-08a — `e2e-demo` still on routing pilot allowlist ⭐ P0

**Files:** `review_agent/.env.example` L263 · `config_advisory.py` L63–72 · `routing_tenant.py` L12–21

Golden example still ships:

```env
OBLIGATION_ROUTING_TENANT_ALLOWLIST=e2e-demo,atlassian-demo,xecurify-demo
```

RC-0304 moved fixtures to `atlassian-demo` / `xecurify-demo`, but Dev UI default (`E2E_TENANT_ID=e2e-demo`) and legacy allowlist keep routing **enabled** on the shared polluted tenant. E2b fires as **info**, not a block.

**Effect:** Semantic planner + catalog match run against 26–29 policy titles on legacy tenant → `routing_or_skip`, `low_concept_overlap`, or wrong candidates. Amplifies RC-04; not sole cause (P5 clean tenant got 6 NC with routing-capable config).

---

### RC-08b — `parallel_hybrid` applies to all tenants when allowlist empty ⭐ P0

**Files:** `pipeline_mode.py` L9–15 · `temp_java_sync/.env.example` L44

```python
def parallel_pipeline_active(tenant_id, settings):
    if settings.review_pipeline_mode != "parallel_hybrid":
        return False
    allow = _parse_tenant_list(settings.review_pipeline_tenant_allowlist)
    if allow and tenant_id not in allow:
        return False
    return True  # empty allowlist → ALL tenants parallel
```

Battery `.env.example` sets `REVIEW_PIPELINE_MODE=parallel_hybrid` with **no** `REVIEW_PIPELINE_TENANT_ALLOWLIST`. P5 rerun on polluted 26-policy index + parallel → obligation/section join races wrong partial state → `compare_queued=0`.

**Effect:** PF-1C parallel on wrong catalog is worse than serial; operator has no guardrail.

---

### RC-08c — IPC-1 planner cascade unbounded on discovery-only scope ⭐ P1

**Files:** `semantic_routing_planner.py` L98–107 · `routing_scope.py` L9–23 · `routing_nodes.py` L88–90

When `policy_document_ids` empty, `review_catalog_doc_ids()` falls back to `discovered_policy_document_ids`. On polluted tenant discovery returns 26+ IDs → planner `_policy_titles_block(catalog_entries)` sends **26 titles** per batch → wrong intent/concepts → retrieval miss.

P5 baseline: `routing_planner_calls=0` (alias + evidence path sufficient on 9-policy scope).

**Effect:** Planner LLM spend **↑** with accuracy **↓** on polluted discovery scope. Wall time wasted, not saved.

---

### RC-09a — Section compare uses retrieval hits without request-scope doc filter ⭐ P0

**Files:** `section_compare_llm.py` L537–546 · `compare_hit_selection.py` L106–138 · `section_retrieval_nodes.py` L38–41

Retrieval passes `scope_ids` to MCP, but compare path `filter_hits_for_compare()` only applies category/trusted gates — **no** intersection with `state.policy_document_ids`.

When index is mixed (or discovery over-broad), LLM compare runs with **wrong policy text** in prompt. Compare **ran** (`compare_items=28`) but outcome is IPC / wrong NC.

**Contrast:** Obligation path already scopes catalog via `routing_scope.review_catalog_doc_ids()` (RC-0304 F5).

---

### RC-09b — Playbook hints not scoped to review policy set ⭐ P1

**Files:** `section_compare_nodes.py` L249+ · `playbook_context.py`

Playbook hints loaded from state may include documents outside the 9-policy request scope. Sections 15/19/20.4 are playbook-sensitive (indemnity, publicity, governing law).

**Effect:** LLM sees contradictory playbook guidance → IPC or missed NC.

---

### RC-09c — Token batch splits under HOT shrink effective context, not batch size ⭐ P2 diagnostic

**Files:** `token_budget.py` L29–41 · `section_compare_llm.py` L561–570

Phase D reduces `effective_compare_max_tokens` to 85% in HOT posture (post-429). Same `section_compare_batch_size=4`, but **more batches** with different section groupings after partial 429 inheritance from prior Cisco run.

**Not a batch-size config change** — operator constraint honored. Stabilizing **sort order** before split avoids arbitrary grouping shifts; does not reduce configured batch size.

---

### RC-10a — Empty LLM body classified as STRUCTURE but HOT blocks single retry ⭐ P0

**Files:** `obligation_extract.py` L179–203 · `failure_policy.py` L128–165 · `llm_gateway.py` L214–217

Batch failure message `Expecting value: line 1 column 1` → `FailureClass.STRUCTURE`. Recovery path exists:

```python
if should_batch_single_retry(...):  # batch → per-section
```

Under HOT posture after Cisco 429s, `llm_hot_structure_split_max` (default 12) caps structure splits. Extract batches (6 sections) hit cap → **fallback obligations** (`extract_source="fallback"`) for whole batch.

**Effect:** 117 obligations counted; many are low-quality fallback spans → planner noise + weak routing (RC-08 amplifier).

---

### RC-10b — Gateway does not treat empty content as retriable structure ⭐ P1

**Files:** `llm_gateway.py` `_invoke_once` L214–217

```python
content = getattr(response, "content", "")
if not isinstance(content, str):
    raise ValueError("LLM returned non-text content")
data = _extract_json_payload(content)  # "" → JSONDecodeError at char 0
```

No single-shot retry on empty body before surfacing to batch layer. One cheap retry avoids full batch→N singles when model returns blank after rate-limit edge.

---

### RC-10c — Extract quality invisible in diagnosis ⭐ P2

**Files:** `obligation_nodes.py` L195–197 · `engine_diagnosis.py`

Counters `extract_batch_failures`, `extract_single_recovered` exist in compliance_stats but not surfaced in Phase G baseline interpretation or golden gates.

---

## 3. Fix tasks

### RC8-F1 — Remove legacy tenant from golden routing allowlist ⭐ P0 · ~8 LOC

**Files:** `review_agent/.env.example`, `temp_java_sync/bootstrap_env.py`

```python
def apply_golden_tenant_rollout_defaults() -> None:
    if not os.environ.get("OBLIGATION_ROUTING_TENANT_ALLOWLIST", "").strip():
        os.environ["OBLIGATION_ROUTING_TENANT_ALLOWLIST"] = "atlassian-demo,xecurify-demo"
    if not os.environ.get("OBLIGATION_ROUTING_TENANT_DENYLIST", "").strip():
        os.environ["OBLIGATION_ROUTING_TENANT_DENYLIST"] = "e2e-demo"
```

Update `.env.example`:

```env
OBLIGATION_ROUTING_TENANT_ALLOWLIST=atlassian-demo,xecurify-demo
OBLIGATION_ROUTING_TENANT_DENYLIST=e2e-demo
```

Call from `apply_golden_review_defaults()` in battery / `run_atlassian_review.py`.

**Acceptance:** `obligation_routing_active("e2e-demo", settings)` → False with golden defaults; True for `atlassian-demo`.

---

### RC8-F2 — PF-1C parallel allowlist in golden harness ⭐ P0 · ~10 LOC

**Files:** `temp_java_sync/.env.example`, `bootstrap_env.py`

```env
REVIEW_PIPELINE_MODE=parallel_hybrid
REVIEW_PIPELINE_TENANT_ALLOWLIST=atlassian-demo,xecurify-demo
```

Wire in `apply_golden_tenant_rollout_defaults()`. Unlisted tenants stay **serial** even when mode is parallel_hybrid.

**Acceptance:** `parallel_pipeline_active("e2e-demo", settings)` → False; `atlassian-demo` → True.

**Wall time:** Neutral or ↓ (no parallel join on polluted tenant).

---

### RC8-F3 — Config advisory E2d (legacy shared tenant) ⭐ P1 · ~15 LOC

**Files:** `config_advisory.py`

Warn when:

```python
tenant_id == "e2e-demo"
and settings.obligation_routing_enabled
```

```text
rule_id=E2d, severity=warn
message="Legacy shared tenant e2e-demo — use atlassian-demo/xecurify-demo; routing disabled by denylist"
```

Upgrade E2b to **warn** (not info) when tenant is allowlisted **and** `OBLIGATION_ROUTING_TENANT_DENYLIST` is empty (unsafe pilot posture).

**Acceptance:** `test_config_advisory.py::test_e2d_legacy_tenant_warns`

---

### RC8-F4 — IPC-1 planner deferral on oversized discovery scope ⭐ P1 · ~25 LOC

**Files:** `routing_nodes.py` `semantic_route_node`, `config.py`

Skip `plan_obligation_routing` LLM when **all** of:

- no explicit `policy_document_ids` in state
- `len(review_catalog_doc_ids(state) or []) > routing_planner_max_catalog_policies` (new setting, default **12**)
- alias pass did not resolve obligation

Use `_fallback_plan()` (existing IPC-1 path) instead of planner batch.

```python
routing_planner_max_catalog_policies: int = 12  # 0 = disable guard
```

**Preserves:** Non-deterministic planner when catalog ≤12 (P5-scale). **No batch size change.**

**Wall time:** ↓ on polluted runs (skip planner batches); unchanged on clean 9-policy run (planner still eligible).

**Acceptance:** Unit test — 26-entry discovered scope → `planner_calls()==0`; 9-entry → planner may run.

---

### RC8-F5 — Post-run flag `routing_on_oversized_catalog` ⭐ P2 · ~20 LOC

**Files:** `baseline_interpretation.py`, `engine_diagnosis.py`

Flag when `routing_planner_calls > 0` and `policies_discovered > 12` and `discovery_scope_mode != "request"`.

**Acceptance:** LIVE polluted artifact flags; P5 good run does not.

---

### RC9-F1 — Request-scope hit filter at section compare ⭐ P0 · ~30 LOC

**Files:** `compare_hit_selection.py`, `section_compare_llm.py`, `section_compare_nodes.py`

Add optional `allowed_document_ids: set[str] | None` to `filter_hits_for_compare()`:

```python
if allowed_document_ids:
    hits = [h for h in hits if str(h.parent_chunk.document_id) in allowed_document_ids]
```

Pass `review_catalog_doc_ids(state)` from `section_compare_nodes.py` into `compare_all_sections()`.

**When scope None:** unchanged behavior (indexed discovery reviews).

**Acceptance:** Test — section has hits from doc A+B; scope={A} → compare prompt only includes A.

**Forbidden:** Do not lower category overlap thresholds to “force hits.”

---

### RC9-F2 — Scope playbook hints to review policy set ⭐ P1 · ~15 LOC

**Files:** `section_compare_nodes.py` `_playbook_hints()`

```python
scope = review_catalog_doc_ids(state)
if scope:
    hints = {k: v for k, v in hints.items() if k in scope}
```

**Acceptance:** Hints dict keys ⊆ `policy_document_ids` on request-scoped Atlassian run.

---

### RC9-F3 — Golden per-section status gates (15, 19, 20.4) ⭐ P1 · ~35 LOC

**Files:** `temp_java_sync/golden_thresholds.json`, `validate_p5_golden.py`, `export_assessment.py`

Add `_assert_section_status_floors()`:

| section_id | min_status |
|------------|------------|
| 15 | NON_COMPLIANT |
| 19 | NON_COMPLIANT |
| 20.4 | NON_COMPLIANT |

Only when `baseline_profile=atlassian_v1` and `compare_items >= 20`.

**Acceptance:** Battery fails if any of three sections IPC on Atlassian.

---

### RC9-F4 — Phase G flag `section_compare_wrong_universe` ⭐ P2 · ~25 LOC

**Files:** `baseline_interpretation.py`

When:

```python
compare_items >= 15
and policies_discovered > 12
and discovery_scope_mode != "request"
and "section_nc_regression" in health_flags
```

Add flag to distinguish RC-09 from RC-06 pure masking.

---

### RC9-F5 — Stable section order before token split ⭐ P2 · ~8 LOC

**Files:** `token_budget.py` `split_batch_by_token_budget()`

Sort `compare_sections` by `section_id` (lexical) **once** before greedy split. Same configured `section_compare_batch_size` and `section_compare_max_tokens`.

**Not deterministic routing** — only stabilizes batch grouping under token cap after 429.

**Acceptance:** Same sections → same batch grouping across runs with fixed input hits.

---

### RC10-F1 — Structure single-retry budget for obligation extract ⭐ P0 · ~20 LOC

**Files:** `failure_policy.py`, `config.py`

Add stage-aware cap:

```python
def should_batch_single_retry(..., stage: str = "default"):
    ...
    if stage == "obligation_extract" and failure_class == FailureClass.STRUCTURE:
        if posture == ReviewPosture.DEGRADED:
            return False
        return True  # exempt from llm_hot_structure_split_max
```

Pass `stage="obligation_extract"` from `obligation_extract.py`.

**Rationale:** Extract single-retry is **recovery**, not parallel compare amplification. Does not increase quota events (single-section calls replace failed batch, not add concurrent load).

**Wall time:** ↑ slightly on structure failure only; ↓ vs full fallback quality loss + downstream planner waste.

---

### RC10-F2 — Gateway empty-body structure retry ⭐ P1 · ~18 LOC

**Files:** `llm_gateway.py` `_invoke_once()`

After first invoke, if `content.strip() == ""`:

```python
logger.warning("LLM empty body — one structure retry")
# single re-invoke (same messages), no extra quota backoff loop
```

Count as structure attempt; do not increment rate_limit_events.

**Acceptance:** Mock empty response → second invoke succeeds → schema validates.

---

### RC10-F3 — Diagnosis + golden extract quality floors ⭐ P1 · ~25 LOC

**Files:** `engine_diagnosis.py`, `baseline_interpretation.py`, `golden_thresholds.json`

Expose in `obligation_pipeline.extract_quality`:

```json
{
  "batch_failures": 2,
  "single_recovered": 10,
  "fallback_count": 0,
  "llm_extract_rate": 0.95
}
```

Golden gate (Atlassian): `extract_fallback_count == 0` OR `llm_extract_rate >= 0.90`.

Health flag `extract_structure_degraded` when `batch_failures >= 2` and `llm_extract_rate < 0.85`.

---

### RC10-F4 — Config advisory E9 (extract fallback on large contract) ⭐ P2 · ~12 LOC

**Files:** `config_advisory.py` — post-run hook via diagnosis export, or warn when `OBLIGATION_EXTRACT_BATCH_RETRY_SINGLE=false`.

---

### RC-F10 — Tests ⭐ P0 · ~90 LOC

| Test file | Covers |
|-----------|--------|
| `tests/test_routing_tenant.py` | RC8-F1 denylist |
| `tests/test_pipeline_mode.py` (new) | RC8-F2 parallel allowlist |
| `tests/test_config_advisory.py` | RC8-F3 E2d |
| `tests/test_routing_discovery_order.py` | RC8-F4 planner deferral |
| `tests/test_compare_hit_selection.py` | RC9-F1 scope filter |
| `tests/test_obligation_extract.py` | RC10-F1 HOT structure retry |
| `tests/test_llm_gateway.py` | RC10-F2 empty body |
| `tests/test_baseline_interpretation.py` | RC8-F5, RC9-F4, RC10-F3 flags |
| `temp_java_sync/tests/test_golden_section_floors.py` | RC9-F3 |

---

## 4. Implementation order

| Priority | Task | Fixes | LOC |
|----------|------|-------|-----|
| **P0** | RC8-F1 routing allowlist + denylist | RC-08 · E | 8 |
| **P0** | RC8-F2 parallel tenant allowlist | RC-08 · PF-1C | 10 |
| **P0** | RC9-F1 compare hit scope filter | RC-09 · IPC-2/3 | 30 |
| **P0** | RC10-F1 extract structure retry exempt | RC-10 · C | 20 |
| **P0** | RC-F10 core tests | lock | 50 |
| **P1** | RC8-F3 E2d advisory | RC-08 · E | 15 |
| **P1** | RC8-F4 planner deferral on large catalog | RC-08 · IPC-1 | 25 |
| **P1** | RC9-F2 playbook hint scope | RC-09 | 15 |
| **P1** | RC9-F3 golden section floors | IPC-5 | 35 |
| **P1** | RC10-F2 gateway empty retry | RC-10 · B | 18 |
| **P1** | RC10-F3 extract quality diagnosis | IPC-5 | 25 |
| **P2** | RC8-F5 · RC9-F4 · RC9-F5 · RC10-F4 | ops | 65 |

**Total:** ~175 prod/harness, ~90 test.

**Validate only after:** RC-0304 + RC-050607 + B-RC on same agent restart.

---

## 5. What NOT to do

| Action | Why |
|--------|-----|
| Remove `e2e-demo` from Dev UI without migration note | Breaks ad-hoc demos — use denylist, not delete tenant |
| Disable obligation routing globally | P5 accuracy path uses routing on **clean** tenants |
| Lower `evidence_min_*` or `compare_max_policy_hits` | False compares / wrong policy (Phase F forbids) |
| Reduce `obligation_extract_batch_size` or `section_compare_batch_size` | User constraint; does not fix universe |
| Force deterministic planner (temperature 0, fixed sort on routing) | User constraint on routing |
| Skip section compare when hits wrong | Hides failures — filter hits, don’t skip compare |
| Disable `parallel_hybrid` entirely | PF-1C value on allowlisted clean tenants |
| Re-run on `e2e-demo` without `replace_policies=True` | RC-04 returns |

---

## 6. Success metrics (Atlassian golden, full stack)

| Metric | P5 baseline | LIVE (bad) | Target |
|--------|-------------|------------|--------|
| `policies_discovered` | 9 | 26–29 | **≤ 10** |
| `discovery_scope_mode` | request | indexed | **request** |
| `routing_planner_calls` | 0 | many | **≤ 5** on clean 9-policy |
| `compare_items` | ~28 | 28 | **≥ 25** |
| Section 15 / 19 / 20.4 | NC | IPC | **NC** |
| `extract_fallback_count` | 0 | >0 | **0** |
| `extract_batch_failures` | 0 | ≥1 | **0** (or single_recovered ≥ failures) |
| `compare_queued` | 34+ | 0 | **≥ 20** |
| NON_COMPLIANT total | 6 | 1–4 | **≥ 6** |
| Review wall time | ~28 min | ~4.6 min* | **≤ 28 min** |

\*Short wall time with zero compare + degraded extract is **failure**, not optimization.

---

## 7. Validation procedure

1. Restart review agent after env changes (RC8-F1/F2).
2. Confirm `e2e-demo` review: routing **off**, pipeline **serial**, E2d warn if routing env mis-set.
3. `python run_atlassian_review.py` on `atlassian-demo`:
   - No extract JSON parse warnings
   - Sections 15, 19, 20.4 → NC
   - `policies_discovered=9`, `discovery_scope_mode=request`
4. `python run_live_contract_battery.py` — all golden gates pass including RC9-F3 section floors.
5. Regression:

```bash
cd Legal/review/review_agent
pytest tests/test_routing_tenant.py tests/test_compare_hit_selection.py \
  tests/test_obligation_extract.py tests/test_baseline_interpretation.py -q

cd Legal/temp_java_sync
pytest tests/test_golden_section_floors.py tests/test_battery_tenant_scope.py -q
```

6. Optional stress: run Cisco then Atlassian back-to-back — RC10-F1 should recover extract under HOT without fallback flood.

---

## 8. Rollback

1. Restore `OBLIGATION_ROUTING_TENANT_ALLOWLIST=e2e-demo,...` in `.env.example`.
2. Clear `OBLIGATION_ROUTING_TENANT_DENYLIST` and `REVIEW_PIPELINE_TENANT_ALLOWLIST`.
3. Set `routing_planner_max_catalog_policies=0` (disable RC8-F4 guard).
4. Remove `allowed_document_ids` parameter (RC9-F1) — compare uses all hits again.
5. Revert RC10-F1 stage exempt — structure retry respects global HOT cap again.

---

## 9. Phase map

| Phase | Role |
|-------|------|
| **E (E2b/E2d)** | RC8-F1/F3 — tenant rollout guards + operator advisories |
| **PF-1C** | RC8-F2 — parallel only on clean allowlisted tenants |
| **IPC-1** | RC8-F4 — planner deferral on oversized discovery catalog |
| **IPC-2/3** | RC9-F1/F2 — hit + playbook scope alignment |
| **D** | RC9-F5 — stable token batch grouping (not smaller batches) |
| **B** | RC10-F2 — empty-body retry; fewer structure failures entering HOT |
| **C** | RC10-F1 — batch→single extract recovery under HOT |
| **IPC-5 / G** | RC8-F5, RC9-F4, RC10-F3 — flags + golden floors |
| **RC-0304** | **Prerequisite** — isolated tenants + request scope |
| **RC-050607** | **Prerequisite** — cap 80 + F5 recovery + NC flags |

---

## 10. Dependency graph

```text
RC-0304 (clean tenant + request scope)
    ├── RC-08 (routing/parallel allowlist + planner deferral)
    │       └── ↓ planner noise on wrong catalog
    ├── RC-09 (compare hit + playbook scope)
    │       └── ↑ sections 15/19/20.4 → NC
    └── RC-10 (extract structure recovery)
            └── ↑ obligation quality → ↓ routing_or_skip
                    └── RC-050607 F5 tail (downstream)
B-RC (quota posture) ── HOT cap ── RC10-F1 breaks extract recovery loop
```

---

## One-line truth

**RC-08 wastes routing/planner LLM on the wrong catalog; RC-09 runs section compare against the wrong policy hits; RC-10 silently degrades obligations via structure fallbacks — fix tenant rollout guards, scope compare inputs to the request policy set, and recover extract batches under HOT without shrinking batch sizes.**
