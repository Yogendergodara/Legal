# Phase B-RC — Quota Exhaustion & HOT Posture Accuracy Fix (RC-01 / RC-02)

**Version:** 1.0  
**ID:** `DR-PHASE-B-RC`  
**Parent:** [PHASE_B_RETRY_RESILIENCE_PLAN.md](./PHASE_B_RETRY_RESILIENCE_PLAN.md) · [PHASE_E_CONFIG_OPERATOR_GUARD_PLAN.md](./PHASE_E_CONFIG_OPERATOR_GUARD_PLAN.md) · [PHASE_F_ACCURACY_PATHS_GUARD_PLAN.md](./PHASE_F_ACCURACY_PATHS_GUARD_PLAN.md) · [PHASE_G_ATLASSIAN_BASELINE_INTERPRETATION_PLAN.md](./PHASE_G_ATLASSIAN_BASELINE_INTERPRETATION_PLAN.md)  
**Targets:** RC-01 (429 battery exhaustion) · RC-02 (HOT blocks batch→single recovery)  
**Status:** **IMPLEMENTED**  
**Scope:** Production-grade quota pacing + review-scoped posture — **no batch size reduction**, **non-deterministic**, **accuracy preserved**, **wall time not increased** (target: ↓ vs retry storms)  
**Effort:** ~1 engineering day + one Atlassian golden re-run  
**Risk:** Low (tight changes to reset scope + split policy; rollback via config flags)

---

## 1. Problem statement

Battery and multi-review Dev UI sessions show **accuracy collapse** (Atlassian 6→1 NC) while **429 events** climb (NDA 52, EULA 31). Phase B posture is **working as designed** but two **gaps** turn infra protection into **false IPC**:

| RC | Symptom | Misread |
|----|---------|---------|
| **RC-01** | 429 storm across 5 contracts | “Need bigger batches / more concurrency” |
| **RC-02** | HOT/DEGRADED → no batch→single | “Posture is broken” |

**Not acceptable fixes:**

- Shrink `section_compare_batch_size`, `obligation_extract_batch_size`, etc.
- Disable Phase B posture globally (`LLM_REVIEW_POSTURE_ENABLED=false`) → retry spiral returns
- Disable compare on 429 → accuracy loss

**Acceptable fixes:**

- **Review-scoped** quota counters (each review starts fresh posture)
- **Selective** batch→single under HOT for **STRUCTURE/UNKNOWN only** (not QUOTA fan-out)
- **Adaptive** pacing when already hot (jitter, not fixed sleep)
- **Harness** spacing between contracts (CI only)
- **Fail-open** on tail paths (grounding quote repair) so one 429 doesn’t abort battery

---

## 2. Code-proven root causes

### RC-01a — `rate_limit_events` leak across reviews ⭐ P0

**Files:** `failure_policy.py` L78–79 · `review_graph.py` L216 · `llm_gateway.py` L37–58

```python
def reset_review_llm_counters() -> None:
    _batches_failed.set(0)  # ← does NOT reset limiter.rate_limit_events
```

`run_review()` calls `reset_review_llm_counters()` but the **global limiter** keeps cumulative events. Cisco review → 10+ events → Atlassian starts in **HOT** before first LLM call.

**Effect:** RC-01 amplified across battery; diagnosis `llm_rate_limit_events` mixes multiple reviews in one process.

---

### RC-01b — Shared Mistral quota, burst concurrency ⭐ P0

**Files:** `llm_gateway.py` L244–286 · `config.py` L35–39

All stages share one semaphore (`llm_global_concurrency=2`). Battery runs **5 reviews without cooldown** → provider 1300/429.

**Effect:** Compare LLM fails → IPC/INCONCLUSIVE (RC-06 downstream).

**Fix layer:** pacing + harness spacing — **not** batch size.

---

### RC-02a — HOT blocks **all** batch→single splits ⭐ P0

**Files:** `failure_policy.py` L98–108

```python
def allow_batch_single_split(...):
    if posture != ReviewPosture.NORMAL:
        return False  # ← blocks STRUCTURE too
    return failure_class in (FailureClass.STRUCTURE, FailureClass.UNKNOWN)
```

Under HOT (≥3 events), **structure failures** (empty JSON `"Expecting value: line 1 column 1"`) **cannot** split batch→single. Batch compare fails entirely → false IPC.

**Observed:** ATL LIVE extract fail + section paths; P5 had structure split recovery when posture NORMAL.

**QUOTA** batch failures must **stay** blocked under HOT (no fan-out) — only STRUCTURE/UNKNOWN split allowed.

---

### RC-02b — Tail paths hard-fail on 429 ⭐ P1

**Files:** `quote_repair_llm.py` · `grounding_quote.py` (via `nodes.py` grounding_node)

ULA battery **crashed** on unhandled 429 in quote repair. Section compare already fail-opens to IPC; grounding does not.

**Effect:** RC-01 aborts entire battery; remaining contracts untested.

---

## 3. Design principles

### 3.1 Non-deterministic (required)

| Mechanism | Why |
|-----------|-----|
| Existing jitter on 429 backoff | `llm_gateway.py` L276–279 |
| Adaptive harness cooldown | `min(120, events * 2)` seconds between battery contracts — not fixed 10 min |
| HOT structure split **cap** per review | Max N splits under DEGRADED — dynamic, not “always split all” |
| Posture from **live** event count | Re-read limiter inside retry loop where safe |

No static “always wait 60s between batches.”

### 3.2 Do not reduce batch sizes

| Unchanged | Reason |
|-----------|--------|
| `section_compare_batch_size` (8) | Phase D token batching assumes this |
| `obligation_extract_batch_size` (6) | Throughput |
| `guard_pass_batch_size`, quote repair batch sizes | Phase C consolidation |

Throughput preserved; **fewer wasted retries** lowers wall time.

### 3.3 Accuracy invariants

| Invariant | Enforcement |
|-----------|-------------|
| QUOTA failures never batch→single under HOT/DEGRADED | `FailureClass.QUOTA` guard unchanged |
| Atlassian NC ≥ 6 on golden | Phase G primary_accuracy |
| `llm_rate_limit_events` per review ≤ 25 | Phase G golden band |
| F5 tail still runs | Phase F — no defer guard/repair under HOT in v1 |

### 3.4 Time budget (must not increase)

| Source of slowness today | Fix reduces |
|--------------------------|-------------|
| 429 → 4 retries × backoff | Per-review reset + conservative profile → fewer events |
| Retry storm on empty JSON batch | STRUCTURE split under HOT → 1 batch + k singles vs full batch fail + F5 miss |
| Battery starting HOT | Reset events → first contract clean posture |

**Target:** Atlassian golden **≤ baseline wall time** with **≥ baseline NC** and **events ≤ 25**.

---

## 4. Implementation tasks

### B-RC-F1 — Review-scoped limiter reset ⭐ P0

| Field | Detail |
|-------|--------|
| **Files** | `llm_gateway.py`, `failure_policy.py`, `review_graph.py` |
| **Change** | `reset_review_llm_counters()` also zeros `_limiter.rate_limit_events` when limiter exists |
| **API** | Optional `reset_llm_limiter_events()` in gateway; call from existing `run_review` reset block |
| **LOC** | ~12 prod |
| **Acceptance** | Two sequential mocked reviews: review2 starts posture NORMAL even if review1 had 10 events |
| **Rollback** | Config `llm_review_scope_reset_events: bool = True` (default True) |

```python
def reset_review_llm_counters() -> None:
    _batches_failed.set(0)
    from review_agent.models.llm_gateway import reset_limiter_rate_limit_events
    reset_limiter_rate_limit_events()
```

---

### B-RC-F2 — HOT allows STRUCTURE/UNKNOWN batch→single ⭐ P0

| Field | Detail |
|-------|--------|
| **Files** | `failure_policy.py` |
| **Change** | Replace blanket `posture != NORMAL` with class-aware rules |

```python
def allow_batch_single_split(failure_class, posture, *, enabled=True) -> bool:
    if not enabled:
        return True
    if failure_class == FailureClass.QUOTA:
        return posture == ReviewPosture.NORMAL  # never fan-out on 429 under pressure
    if failure_class in (FailureClass.STRUCTURE, FailureClass.UNKNOWN):
        return posture != ReviewPosture.DEGRADED  # HOT ok, DEGRADED blocked
    return posture == ReviewPosture.NORMAL
```

| **LOC** | ~15 prod |
| **Acceptance** | `test_failure_policy`: STRUCTURE+HOT → True; QUOTA+HOT → False; STRUCTURE+DEGRADED → False |
| **Callers unchanged** | `section_compare_llm.py`, `obligation_extract.py`, `obligation_compare_llm.py`, `section_classifier.py` |

**Accuracy:** Recovers batch JSON/schema failures without multiplying 429 calls.

---

### B-RC-F3 — DEGRADED structure split cap (optional safety) ⭐ P1

| Field | Detail |
|-------|--------|
| **Files** | `failure_policy.py`, `config.py` |
| **Config** | `llm_hot_structure_split_max: int = 12` (0 = unlimited under HOT) |
| **Change** | ContextVar counter; increment on each allowed HOT structure split; block when cap reached |
| **LOC** | ~20 prod |
| **Acceptance** | 13th structure split under HOT blocked; review completes |
| **Default** | 12 — enough for 1–2 failed compare batches without full fan-out |

Reset counter in B-RC-F1 per review.

---

### B-RC-F4 — Adaptive micro-pause before acquire when HOT ⭐ P1

| Field | Detail |
|-------|--------|
| **Files** | `llm_gateway.py`, `config.py` |
| **Config** | `llm_hot_acquire_pause_enabled: bool = True`, `llm_hot_acquire_pause_max_seconds: float = 1.5` |
| **Change** | Before `async with limiter.semaphore`, if `get_current_review_posture() == HOT`: sleep `min(max, 0.3 * events)` + jitter(0, 0.2) |

**Non-deterministic**, **only when already hot**, **does not change batch sizes**. Smooths burst so next call less likely to 429.

| **LOC** | ~18 prod |
| **Acceptance** | Mock: HOT review has spaced acquire timestamps; NORMAL has no pause |
| **Time impact** | +0–1.5s per LLM call under HOT only; saves multi-second retry loops |

---

### B-RC-F5 — Grounding / quote repair 429 fail-open ⭐ P1

| Field | Detail |
|-------|--------|
| **Files** | `quote_repair_llm.py`, `grounding_quote.py` (or `nodes.py` grounding_node wrapper) |
| **Change** | Catch 429 / `LLMUnavailableError` in batch quote repair → log warning, skip repair, keep finding (mirror section compare fail-open) |
| **LOC** | ~25 prod |
| **Acceptance** | Mock 429 in quote repair: review completes, no graph crash |
| **Accuracy** | Quotes may stay ungrounded; F5/guard can still run — better than abort |

---

### B-RC-F6 — Battery harness: adaptive cooldown + profile ⭐ P1

| Field | Detail |
|-------|--------|
| **Files** | `run_live_contract_battery.py`, `validate_p5_golden.py`, `bootstrap_env.py` |
| **Change** | After each contract, read last review `resilience.llm_rate_limit_events`; if ≥ `battery_cooldown_event_threshold` (default 8): sleep `min(120, events * 2)` seconds with log |
| **Env** | `GOLDEN_LLM_RATE_LIMIT_PROFILE=mistral_conservative` for battery scripts only (explicit env in script if unset) |
| **LOC** | ~35 prod/harness |
| **Acceptance** | 5-contract battery completes; Atlassian not started with inherited HOT from Cisco |
| **Production** | Harness-only; single-tenant prod unaffected |

---

### B-RC-F7 — Dynamic posture in gateway 429 retry loop ⭐ P2

| Field | Detail |
|-------|--------|
| **Files** | `llm_gateway.py` L262–267 |
| **Change** | On each 429 retry attempt, recompute `review_posture` from **current** `limiter.rate_limit_events` for `gateway_max_attempts` |
| **LOC** | ~8 prod |
| **Acceptance** | First 429 in call still allows retries; after events cross HOT threshold mid-call, attempts cap to 1 on subsequent 429s in **same** invoke |

Prevents late-call retry spiral without changing batch sizes.

---

### B-RC-F8 — Diagnosis clarity ⭐ P2

| Field | Detail |
|-------|--------|
| **Files** | `engine_diagnosis.py`, `failure_policy.py` |
| **Change** | Add `resilience.llm_review_posture` (already in enrich) + `resilience.llm_hot_structure_splits_used` when F3 enabled |
| **LOC** | ~10 prod |
| **Acceptance** | Diagnosis shows posture at report time; ops can correlate false IPC with HOT |

---

### B-RC-F9 — Tests ⭐ P0

| Field | Detail |
|-------|--------|
| **Files** | `test_failure_policy.py`, `test_llm_gateway_rate_limit.py`, new `test_review_scope_reset.py` |
| **Cases** | F1 cross-review reset; F2 STRUCTURE+HOT split; F2 QUOTA+HOT no split; F3 cap; F4 pause only when HOT; F5 quote repair 429 no crash |
| **LOC** | ~70 test |

---

## 5. Priority & effort

| Priority | Task | Fixes | LOC |
|----------|------|-------|-----|
| **P0** | B-RC-F1 review-scoped reset | RC-01a battery bleed | 12 |
| **P0** | B-RC-F2 HOT structure split | RC-02a false IPC | 15 |
| **P0** | B-RC-F9 tests | lock behavior | 70 |
| **P1** | B-RC-F4 adaptive micro-pause | RC-01b burst | 18 |
| **P1** | B-RC-F5 quote repair fail-open | RC-02b crash | 25 |
| **P1** | B-RC-F6 battery cooldown | RC-01 harness | 35 |
| **P1** | B-RC-F3 structure split cap | safety under HOT | 20 |
| **P2** | B-RC-F7 dynamic gateway posture | mid-call spiral | 8 |
| **P2** | B-RC-F8 diagnosis | ops | 10 |

**Total:** ~115 prod, ~70 test — **no batch size changes**.

---

## 6. What NOT to do

| Action | Why |
|--------|-----|
| Reduce batch sizes | User constraint; hurts throughput without fixing quota |
| Allow QUOTA batch→single under HOT | Fan-out → RC-01 worse |
| Disable posture globally | Returns pre-Phase-B retry spiral (51 events baseline) |
| Fixed 60s sleep between every LLM call | Deterministic; increases wall time |
| Reset limiter between every **invoke** | Breaks per-review event accounting mid-review |
| Skip compare on 429 | Accuracy regression |

---

## 7. Success metrics (Atlassian golden after fix)

| Metric | Baseline | Target |
|--------|----------|--------|
| NON_COMPLIANT | 6 | **≥ 6** |
| `llm_rate_limit_events` (per review) | 51 | **≤ 25** |
| Review wall time | ~28 min | **≤ 28 min** (↓ if retry storm removed) |
| `compare_queued` | 34 | **≥ 28** |
| Battery completes 5 contracts | crash on ULA | **exit 0** |
| Posture at start of review 2+ | HOT (bug) | **NORMAL** after F1 |

---

## 8. Rollback

1. `llm_review_scope_reset_events=false` — restore cross-review event bleed (not recommended)
2. Revert F2 — HOT blocks all splits again (legacy Phase B)
3. `llm_hot_acquire_pause_enabled=false`
4. `llm_hot_structure_split_max=0` — unlimited HOT splits (or revert F2 entirely)
5. Remove battery cooldown from harness only

---

## 9. Phase map

| Phase | Role in this plan |
|-------|-------------------|
| **B** | F1–F4, F7 — core posture + reset + pacing |
| **E** | Document `mistral_conservative` for golden; warn if battery concurrency > 2 |
| **F** | F5 fail-open aligns with F4 compare fail-open; F5 tail unchanged |
| **G** | Validate per-review events + NC floor after fix |
| **PF-1A** | Conservative profile defaults for CI battery |
| **IPC-5** | Harness cooldown complements F6 |

---

## One-line truth

**Fix RC-01/02 by scoping quota counters per review and allowing structure-only batch→single under HOT — not by shrinking batches — so accuracy recovers without retry storms or longer wall time.**
