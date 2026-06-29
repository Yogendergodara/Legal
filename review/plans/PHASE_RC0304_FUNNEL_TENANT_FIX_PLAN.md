# Phase RC-03/04 — Obligation Funnel & Tenant Isolation Fix

**Version:** 1.0  
**ID:** `DR-PHASE-RC0304`  
**Parent:** [PHASE_G_ATLASSIAN_BASELINE_INTERPRETATION_PLAN.md](./PHASE_G_ATLASSIAN_BASELINE_INTERPRETATION_PLAN.md) · [PHASE_IPC2_SYNC_INDEX_QUALITY_IMPLEMENTATION_PLAN.md](./PHASE_IPC2_SYNC_INDEX_QUALITY_IMPLEMENTATION_PLAN.md) · [PHASE_IPC3_DISCOVERY_RETRIEVAL_TUNING_PLAN.md](./PHASE_IPC3_DISCOVERY_RETRIEVAL_TUNING_PLAN.md) · [PHASE_IPC5_VALIDATION_OBSERVABILITY_PLAN.md](./PHASE_IPC5_VALIDATION_OBSERVABILITY_PLAN.md) · [PHASE_B_RC0102_QUOTA_POSTURE_FIX_PLAN.md](./PHASE_B_RC0102_QUOTA_POSTURE_FIX_PLAN.md)  
**Targets:** RC-03 (pathological `compare_queued=0`) · RC-04 (shared `e2e-demo` tenant pollution)  
**Status:** **IMPLEMENTED**  
**Scope:** Restore obligation compare funnel without lowering F4 evidence gates, without batch-size reduction, non-deterministic routing preserved, wall time not increased (target: ↓ wasted catalog searches)  
**Effort:** ~0.75 engineering day + one Atlassian golden re-run  
**Risk:** Low–medium (harness-only fixes are P0; graph reorder is isolated wiring change with tests)

---

## 1. Problem statement

Live battery runs show **100% obligation IPC** with **`compare_queued=0`** — not because evidence gates saved LLM calls, but because the **compare path never ran**. Phase G correctly documents this as a funnel failure, not quota success.

| RC | Symptom | Misread |
|----|---------|---------|
| **RC-03** | `compare_queued=0`, skip reasons `routing_or_skip` + `low_concept_overlap` | “IPC 100% = we optimized LLM” |
| **RC-04** | `policies_discovered` 26–32 vs golden 9 | “Discovery is broken” (partially — **tenant** is broken first) |

### Evidence table

| Run | extracted | compare_queued | post_validation_compared | policies_discovered |
|-----|-----------|----------------|--------------------------|---------------------|
| ATL P5 (good) | 80 | 42 | 18 | 9 |
| ATL LIVE | 117 | 0 | 0 | 29 |
| ATL rerun | 169 | 0 | 0 | 26 |
| ULA LIVE | — | — | — | 27 |
| EULA LIVE | — | — | — | 32 |
| NDA LIVE | — | — | — | 28 |

**Causal chain:** RC-04 (wrong catalog) → RC-03 (no obligations reach `decision=compare` in F4 evidence gate).

**Not caused by:** Phases D/E/F algorithm changes, Phase B posture (addressed in B-RC), or quota 429 alone (429 can reduce batches but does not zero `compare_queued` by itself).

---

## 2. Code-proven root causes

### RC-04a — Battery accumulates policies on shared `e2e-demo` ⭐ P0

**Files:** `temp_java_sync/run_live_contract_battery.py` L108–112, L198–220 · `fixtures/atlassian_e2e.json` L2 · `fixtures/xecurify_e2e.json` L2

```python
await sync_policies_only(..., tenant_id=tenant, replace_policies=False)  # ← append-only
# atlassian, ula, eula, nda all use tenant "e2e-demo"
```

Each contract syncs its fixture policies **without replace**. After Cisco (separate tenant), Atlassian adds 9 policies; Xecurify fixtures add ~18 more → **29 indexed policies** on one tenant.

**Effect:** Discovery, catalog registry, and section retrieval all see a **mixed Atlassian + Xecurify catalog**. Wrong `candidate_doc_ids` → wrong retrieval hits → `low_concept_overlap` IPC.

**Contrast (good run):** `run_atlassian_review.py` syncs 9 policies and passes **`policy_document_ids`** from sync into review (request scope).

---

### RC-04b — `run_live_contract_battery` omits review scope ⭐ P0

**Files:** `run_live_contract_battery.py` L72–78 · `run_atlassian_review.py` L110–125

Battery calls:

```python
await run_review(..., policy_scope="indexed")  # no policy_document_ids
```

Golden Atlassian script calls:

```python
await review_text(..., policy_document_ids=policy_ids)  # 9 doc IDs
```

**Effect:** Even with a clean tenant, battery uses topic discovery (adaptive cap ~6–20 groups) instead of the known 9-policy scope used in P5.

---

### RC-03a — Catalog match runs against **full tenant registry** before discovery ⭐ P0

**Files:** `review_graph.py` L102–106 · `routing_nodes.py` L145–172

Current upstream order:

```text
obligation_extract → semantic_route → catalog_match → contract_routing → policy_discovery → index_policies
```

`catalog_match_node` loads **all** indexed policies for the tenant:

```python
catalog_snapshot = await get_catalog_snapshot(client, tenant_id, ...)
allowed = indexed_doc_id_set(catalog)  # entire tenant — not discovered subset
```

Discovery runs **after** catalog match. On a polluted tenant (RC-04), every obligation’s catalog search fans out across 29 policies **before** discovery caps the review set.

**Effect:** `route_decision=ipc` with empty candidates → `routing_or_skip` (47 on LIVE). Non-empty but wrong candidates → retrieval hits fail `evidence_min_concept_overlap` (0.25) → `low_concept_overlap` (46 on LIVE). **Zero** obligations get `decision=compare` → `compare_queued=0`.

---

### RC-03b — F4 evidence gate is working; it was never fed ⭐ diagnostic

**Files:** `evidence_sufficiency.py` L108–147 · `obligation_compare_nodes.py` L110–111

Compare queue is built only from evidence results:

```python
if evidence.decision == "compare":
    compare_queue.append(ob)
```

LIVE skip tally (`routing_or_skip` + `low_concept_overlap`) proves the gate never saw sufficient evidence — **not** that gates are too strict.

**Forbidden fix (Phase F):** Lower `evidence_min_score`, `evidence_min_concept_overlap`, or `routing_ipc_max_confidence` to “force compare.” That creates false compares and wrong-policy violations.

---

### RC-03c — Phase G `compare_funnel_stuck` flag does not catch `compare_queued=0` ⭐ P2

**Files:** `baseline_interpretation.py` L209–217

Existing flag triggers on **high** `compare_queued` + **low** batches (inverse pathology). **`compare_queued=0` with high IPC** needs a distinct health flag for operators.

---

## 3. Mechanism (ASCII)

```text
[POLLUTED TENANT — RC-04]
  e2e-demo: 9 Atlassian + 18 Xecurify + stale = 29 policies

[GRAPH ORDER — RC-03a]
  catalog_match(tenant=29) ──► wrong candidate_doc_ids
         │
         ▼
  obligation_retrieval(wrong fence) ──► hits from Xecurify policy on Atlassian obligation
         │
         ▼
  evidence_sufficiency ──► low_concept_overlap | routing_or_skip
         │
         ▼
  compare_queued = 0  ──► 100% IPC, 0 NC (accuracy collapse)

[GOOD P5 PATH]
  tenant=9 policies OR policy_document_ids=9
  catalog_match scoped ──► valid candidates ──► evidence_sufficient ──► compare_queued≈34
```

---

## 4. Fix strategy (minimal, production-grade)

Two layers — **harness hygiene first** (zero graph risk), then **engine scope** (fixes real multi-policy tenants without gate changes).

| Layer | Phase | Fixes | Wall time |
|-------|-------|-------|-----------|
| **A — Harness / tenant** | IPC-5 · PF-1C | RC-04 | Neutral |
| **B — Catalog scope** | IPC-3 | RC-03 when `policy_document_ids` present | ↓ fewer bad catalog searches |
| **C — Graph order** | IPC-3 | RC-03 indexed mode (discovery before routing) | ↓ or neutral (same nodes, better order) |
| **D — Observability** | IPC-5 · G | Detect pathology early | Neutral |

**Constraints honored:**

- No batch size reduction (`obligation_compare_batch_size`, `section_compare_batch_size`, etc. unchanged)
- Non-deterministic routing preserved (LLM planner, adaptive discovery caps, jitter unchanged)
- No F4 gate lowering
- No “compare on failure” shortcuts

---

## 5. Implementation tasks

### RC-F1 — Dedicated tenant per battery fixture (RC-04) ⭐ P0 · ~25 LOC

**Files:** `fixtures/atlassian_e2e.json`, `fixtures/xecurify_e2e.json`, `run_live_contract_battery.py`

| Fixture | New `tenant_id` |
|---------|-----------------|
| Atlassian | `atlassian-demo` |
| Xecurify (ULA/EULA/NDA) | `xecurify-demo` |
| Cisco | `cisco-beta` (unchanged) |

Battery reads `tenant_id` from fixture JSON (already supported for NDA). **Do not** hardcode `e2e-demo` in the spec tuple.

**Acceptance:** After full battery, `SELECT count(*) FROM policy_documents WHERE tenant_id='atlassian-demo'` = 9 (not 29).

---

### RC-F2 — Replace tenant policies on golden sync (RC-04) ⭐ P0 · ~8 LOC

**Files:** `run_live_contract_battery.py`, optionally `run_atlassian_review.py`

Change sync to:

```python
await sync_policies_only(..., replace_policies=True)
```

Matches existing Dev UI pattern (`test_e2e_harness_tenant.py` expects `replace_tenant_policies=True` for isolated tenants).

**Alternative for shared dev tenant:** `tombstone_tenant_policies()` then sync (`sync_service.py` L37–47) — use only when operator insists on `e2e-demo`.

**Acceptance:** Re-running Atlassian alone on `atlassian-demo` never grows policy count beyond 9.

---

### RC-F3 — Pass `policy_document_ids` from sync into review (RC-04 + RC-03) ⭐ P0 · ~20 LOC

**Files:** `run_live_contract_battery.py`, reuse `review_scope.policy_document_ids_from_sync`

After sync:

```python
policy_ids = policy_document_ids_from_sync(sync_result)
await run_review(..., policy_document_ids=policy_ids, policy_scope="request")
```

Mirror `run_atlassian_review.py` L110–125. Ensures discovery uses **request scope** (`discovery_nodes.py` L63–84) regardless of graph order.

**Acceptance:** Atlassian battery run: `discovery_returned=9`, `discovery_scope_mode=request`.

---

### RC-F4 — Golden preflight: policy count + funnel floors (IPC-5) ⭐ P0 · ~35 LOC

**Files:** `golden_thresholds.json`, `validate_p5_golden.py`, `run_live_contract_battery.py`

Add thresholds:

```json
"atlassian": {
  "max_policies_discovered": 10,
  "min_compare_queued": 28,
  ...
}
```

New assert:

```python
def _assert_discovery_scope(name, diagnosis, thresholds):
    discovered = diagnosis.get("discovery", {}).get("policies_discovered")
    # fail if discovered > max_policies_discovered
```

Wire `_assert_baseline_thresholds` into battery (already exists for `min_compare_queued` — ensure it runs for Atlassian).

**Acceptance:** Polluted run fails at preflight with `policies_discovered 29 > 10` before wasting 28 minutes.

---

### RC-F5 — Scope catalog match to review policy set (IPC-3) ⭐ P1 · ~30 LOC

**Files:** `routing_nodes.py`, new helper in `catalog_registry.py` or `routing_scope.py`

```python
def review_catalog_doc_ids(state: ReviewState) -> set[str] | None:
    ids = [str(x).strip() for x in (state.get("policy_document_ids") or []) if str(x).strip()]
    return set(ids) if ids else None

def filter_catalog_entries(entries: list[CatalogEntry], allowed: set[str]) -> list[CatalogEntry]:
    return [e for e in entries if e.document_id in allowed]
```

In `semantic_route_node` and `catalog_match_node`:

```python
scope = review_catalog_doc_ids(state)
if scope:
    catalog = filter_catalog_entries(catalog, scope)
    allowed = allowed & scope
```

**When `policy_document_ids` empty:** defer to RC-F6 (discovery-first graph).

**Acceptance:** Unit test — tenant has 20 policies, state has 9 `policy_document_ids`, catalog search only returns candidates from those 9.

---

### RC-F6 — Reorder upstream graph: discovery before catalog match (IPC-3) ⭐ P1 · ~15 LOC + tests

**Files:** `review_graph.py` `_wire_upstream`

**New order:**

```text
obligation_extract
  → contract_routing
  → policy_discovery
  → semantic_route
  → catalog_match
  → index_policies
```

Rationale: `policy_discovery_node` needs `contract_routing.topics` (already produced by `contract_routing_node`). After reorder, `discovered_policy_document_ids` is populated; RC-F5 extended to also intersect:

```python
discovered = state.get("discovered_policy_document_ids") or []
if discovered and not scope:
    scope = set(discovered)
```

**Wall time:** Same nodes executed once; catalog match against 9 policies instead of 29 → **fewer** `search_policy_catalog` calls. No new LLM stages.

**Tests:** Update `test_review_graph_parallel_invoke.py` / add `test_routing_discovery_order.py` — assert compiled edge list contains `policy_discovery → semantic_route`.

**Rollback:** Config flag `routing_discovery_before_match=false` restores legacy edge order (default **true** after validation).

---

### RC-F7 — Pathological funnel health flag (Phase G / IPC-5) ⭐ P2 · ~20 LOC

**Files:** `baseline_interpretation.py`, `engine_diagnosis.py`

Add flag when:

```python
extracted >= 20
and compare_queued == 0
and obligation_ipc_rate >= 0.85
```

Flag name: `pathological_ipc_funnel`.

Expose in `engine_diagnosis.ipc_summary`:

```json
"policies_discovered": 9,
"discovery_scope_mode": "request"
```

**Acceptance:** LIVE artifact reproduces flag; P5 good run does not.

---

### RC-F8 — IPC-2 sync quality gate in battery (existing helper) ⭐ P2 · ~10 LOC

**Files:** `run_live_contract_battery.py`, `atlassian_ipc2.validate_policy_sync`

Call `validate_policy_sync` after Atlassian sync (already in `run_atlassian_review.py`). Fail fast on weak keyword tags.

**Acceptance:** Sync with `tagger=keyword` fails before review.

---

### RC-F9 — Tests ⭐ P0 · ~90 LOC

| Test file | Covers |
|-----------|--------|
| `temp_java_sync/tests/test_battery_tenant_scope.py` | F1–F3 fixture tenant + replace + policy_ids passed |
| `tests/test_routing_catalog_scope.py` | F5 scope filter |
| `tests/test_routing_discovery_order.py` | F6 graph edges |
| `tests/test_baseline_interpretation.py` | F7 pathological flag |

---

## 6. Implementation order

| Priority | Task | Fixes | LOC |
|----------|------|-------|-----|
| **P0** | RC-F1 dedicated tenants | RC-04 | 25 |
| **P0** | RC-F2 replace on sync | RC-04 | 8 |
| **P0** | RC-F3 policy_document_ids in battery | RC-03/04 | 20 |
| **P0** | RC-F4 golden preflight | IPC-5 | 35 |
| **P0** | RC-F9 harness tests | lock | 40 |
| **P1** | RC-F5 catalog scope filter | RC-03 | 30 |
| **P1** | RC-F6 graph reorder | RC-03 indexed | 15 |
| **P1** | RC-F9 routing tests | lock | 50 |
| **P2** | RC-F7 pathological flag | G | 20 |
| **P2** | RC-F8 validate_policy_sync in battery | IPC-2 | 10 |

**Total:** ~130 prod/harness, ~90 test — **no batch size changes**, **no F4 gate changes**.

---

## 7. What NOT to do

| Action | Why |
|--------|-----|
| Lower `evidence_min_concept_overlap` / `evidence_min_score` | Phase F forbids; false compares, wrong-policy NC |
| Reduce `catalog_match_min_score` globally | Forces compare on bad matches |
| Disable obligation routing / evidence gate | Hides pathology; accuracy loss |
| Shrink batch sizes | User constraint; unrelated to funnel zero |
| Single shared `e2e-demo` for all battery contracts | RC-04 root cause |
| `replace_policies=False` on golden battery | Accumulates cross-fixture pollution |
| Deterministic fixed policy lists in production graph | Violates non-deterministic routing requirement |

---

## 8. Success metrics (Atlassian golden after fix)

| Metric | Baseline (P5) | LIVE (bad) | Target |
|--------|---------------|------------|--------|
| `policies_discovered` | 9 | 29 | **≤ 10** |
| `compare_queued` | 34 | 0 | **≥ 28** |
| `post_validation_compared` | 18 | 0 | **≥ 12** |
| `routing_or_skip` (top skip) | 41 | 47+ | **≤ 45** (similar; not zero) |
| `low_concept_overlap` | 1 | 46 | **≤ 5** |
| NON_COMPLIANT | 6 | 1 | **≥ 6** |
| Review wall time | ~28 min | ~4.6 min* | **≤ 28 min** (↑ vs broken fast-IPC is OK) |
| Battery exit | fail | fail | **exit 0** |

\*Short LIVE wall time with `compare_queued=0` is a **symptom** of skipped compare LLM — not success.

---

## 9. Validation procedure

1. **Tenant cleanup (one-time operator):** Tombstone stale policies on `e2e-demo` if still used for manual Dev UI, or switch Dev UI to fixture tenants.
2. **Single Atlassian:** `python run_atlassian_review.py` → NC ≥ 6, `compare_queued` ≥ 28.
3. **Full battery:** `python run_live_contract_battery.py` → all gates pass; `live_contract_battery.json` exit 0.
4. **Regression:** `pytest tests/test_routing_catalog_scope.py tests/test_baseline_interpretation.py tests/test_failure_policy.py -q`
5. **Phase G:** `BASELINE_PROFILE=atlassian_v1` — no `pathological_ipc_funnel` flag; no `accuracy_regression`.

---

## 10. Rollback

1. Revert fixture `tenant_id` to `e2e-demo` (harness only — not recommended).
2. `replace_policies=False` in battery (restores pollution).
3. `routing_discovery_before_match=false` — legacy graph order.
4. Remove `max_policies_discovered` from golden thresholds (disables preflight).

Engine changes (F5/F6) are backward-compatible: when no scope IDs and legacy graph order, behavior matches today.

---

## 11. Phase map

| Phase | Role |
|-------|------|
| **G** | F7 pathological flag; funnel story already explains `compare_queued=0` |
| **IPC-2** | F8 sync/tag validation; clean index on replace |
| **IPC-3** | F5 catalog scope; F6 discovery-before-match |
| **IPC-5** | F4 golden floors; battery preflight |
| **PF-1C** | F1–F3 test hygiene; isolated tenants |
| **B-RC** | Prerequisite (quota/HOT); does not fix funnel zero |
| **F** | Evidence gates unchanged — pathology was upstream |

---

## 12. Dependency on B-RC

Phase B-RC (RC-01/02) must remain **implemented** before validating this plan. Otherwise a restored funnel may still lose compares to 429 under HOT. Order of operations:

1. B-RC merged ✓  
2. RC-03/04 (this plan)  
3. Re-run golden battery  

---

## One-line truth

**RC-03 is not high IPC — it is zero compare queue. Fix RC-04 tenant pollution and scope catalog routing to the policies this review actually owns; do not lower evidence gates.**
