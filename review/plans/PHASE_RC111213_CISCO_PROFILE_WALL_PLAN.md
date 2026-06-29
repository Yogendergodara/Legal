# Phase RC-11/12/13 — Cisco Score Collapse, Golden LLM Profile & Fast-Wall Symptom

**Version:** 1.0  
**ID:** `DR-PHASE-RC111213`  
**Parent:** [PHASE_B_RC0102_QUOTA_POSTURE_FIX_PLAN.md](./PHASE_B_RC0102_QUOTA_POSTURE_FIX_PLAN.md) · [PHASE_RC080910_ROUTING_COMPARE_EXTRACT_PLAN.md](./PHASE_RC080910_ROUTING_COMPARE_EXTRACT_PLAN.md) · [PHASE_G_ATLASSIAN_BASELINE_INTERPRETATION_PLAN.md](./PHASE_G_ATLASSIAN_BASELINE_INTERPRETATION_PLAN.md) · [PHASE_E_CONFIG_OPERATOR_GUARD_PLAN.md](./PHASE_E_CONFIG_OPERATOR_GUARD_PLAN.md) · [PHASE_IPC5_VALIDATION_OBSERVABILITY_PLAN.md](./PHASE_IPC5_VALIDATION_OBSERVABILITY_PLAN.md) · [PHASE_R8_R9_IMPLEMENTATION_PLAN.md](./PHASE_R8_R9_IMPLEMENTATION_PLAN.md)  
**Targets:** RC-11 (Cisco legal score 5/10) · RC-12 (default LLM profile under battery) · RC-13 (fast wall time = skipped work)  
**Status:** **IMPLEMENTED**  
**Scope:** Restore Cisco + battery accuracy under quota without lowering batch sizes, non-deterministic routing preserved, wall time **not increased** on successful runs (target: ↓ retry storms; fast runs that skip compare are **failed**, not celebrated)  
**Effort:** ~0.25 day (RC-12 harness profile) + ~0.25 day (RC-11 Cisco gates) + ~0.25 day (RC-13 G flags) + one full battery re-run  
**Risk:** Low (harness + observability); Medium for RC-11 section compare HOT recovery (bounded, mirrors RC-10)

**Prerequisite:** [RC-0304](./PHASE_RC0304_FUNNEL_TENANT_FIX_PLAN.md) · [RC-050607](./PHASE_RC050607_CAP_IPC_F5_RECOVERY_PLAN.md) · [RC-080910](./PHASE_RC080910_ROUTING_COMPARE_EXTRACT_PLAN.md) · [B-RC](./PHASE_B_RC0102_QUOTA_POSTURE_FIX_PLAN.md) deployed.

---

## 1. Problem statement

After tenant isolation and routing fixes, **battery-wide quota posture** and **missing Cisco legal-score gates** still produce **false passes on speed** and **false fails on accuracy**.

| RC | Symptom | Misread |
|----|---------|---------|
| **RC-11** | Cisco LIVE: 24s, legal score **5.0/10**, `compare_items=8`, `llm_batches=2`, F5 recovered **4**; P5: score **6.7**, 10 items, 3 batches; 429 on section compare in battery logs | “Cisco is a small contract — fast is fine” |
| **RC-12** | All LIVE runs: `llm_rate_limit_profile=default`, `llm_global_concurrency=2`; NDA **52** rate-limit events; E4 warns but does not enforce golden profile | “Concurrency 2 is safe because .env.example says so” |
| **RC-13** | ATL LIVE **278s** (~4.6 min) vs baseline **~25 min**; NC **1 vs 6**; P5 rerun 15 min node sum, NC 4 | “Performance improved” |

### Evidence tables

**RC-11 — Cisco section-first collapse**

| Run | wall | legal_score_10 | compare_items | section llm_batches | F5 recovered | 429 |
|-----|------|----------------|---------------|---------------------|--------------|-----|
| Cisco P5 (reference) | — | **6.7** | **10** | **3** | — | low |
| Cisco LIVE (battery) | **24s** | **5.0** | **8** | **2** | **4** | yes (section compare) |
| Gate today | — | hard fail `< 10.0` in `validate_p5_golden` | none | none | none | `max_section_ipc_pct` only |

Cisco uses **section-first** pipeline (`cisco-beta` tenant, no obligation routing). RC-01 manifests on **section compare** path — expected NC sections (CISCO_EXPECTED §1–6) missed when batches fail under 429/HOT.

**RC-12 — profile not applied when `.env` pins `default`**

| Source | `LLM_RATE_LIMIT_PROFILE` | Effective concurrency |
|--------|---------------------------|------------------------|
| `run_live_contract_battery.py` L34–36 | sets only if env **empty** | 2 from `.env.example` |
| Operator `.env` | **`default`** (explicit) | **2** (explicit) |
| B-RC intent | **`mistral_conservative`** | **1** (profile-derived) |
| Good P5 Cisco (historical) | conservative or 4 concurrency on smaller contract | lower 429 |

**Files:** `bootstrap_env.py` · `config.py` `_apply_rate_limit_profile()` L285–298 · `config_advisory.py` E4 L113–124

**RC-13 — fast wall time correlates with skipped funnel work**

| Contract | LIVE wall | P5 baseline wall | LIVE NC | P5 NC |
|----------|-----------|-------------------|---------|-------|
| Atlassian | **278s** | **~1680s** (~28 min) | **1** | **6** |
| P5 rerun | ~900s node sum | — | **4** | **6** |

Mechanism: `compare_queued=0` or minimal section compare + starved F5 → **short wall time, low NC**. Phase G `funnel_story` reports counts but does **not** flag “fast + wrong.”

---

## 2. Code-proven root causes

### RC-11a — No IPC-5 legal-score floor in golden harness ⭐ P0

**Files:** `golden_thresholds.json` L43 · `validate_p5_golden.py` L315–332 · `run_live_contract_battery.py` L388–406

Cisco thresholds today:

```json
"cisco": {"max_section_ipc_pct": 85.0, "min_violations": 4}
```

Battery computes `legal_score_10` via `score_section_expected(CISCO_EXPECTED)` but **does not gate** LIVE on score (only P5 script raises `< 10.0`). LIVE battery passes Cisco with score **5.0** if violations ≥ 4.

**Effect:** RC-11 silent regression; ops sees “battery green” while curated section expectations fail.

---

### RC-11b — Section compare batches drop under 429 without golden floor ⭐ P0

**Files:** `section_compare_nodes.py` · `engine_diagnosis.py` L263–275 · `golden_thresholds.json`

No `min_compare_items` or `min_section_compare_batches` for Cisco. LIVE `compare_items=8` vs P5 **10** — two sections never got compare items (429 or selection IPC).

**Contrast:** Atlassian golden already has `min_compare_queued`, recovery floors (RC-050607).

---

### RC-11c — RC-01 on section-first path during multi-contract battery ⭐ P1

**Files:** `failure_policy.py` · `section_compare_llm.py` L439–444

Section compare uses `should_batch_single_retry(..., stage="default")` — **still subject** to HOT `llm_hot_structure_split_max` (RC-10 fixed **obligation_extract** only).

Cisco runs **first** in battery but can still 429 on first contract if profile=default + concurrency=2 bursts section compare + classify + F5 in one review.

**Effect:** Partial section compare → legal score collapse; F5 only recovers **4** gaps.

---

### RC-12a — Golden profile override defeated by explicit `.env` ⭐ P0

**Files:** `run_live_contract_battery.py` L34–36

```python
if os.environ.get("LLM_RATE_LIMIT_PROFILE", "").strip() == "":
    os.environ["LLM_RATE_LIMIT_PROFILE"] = "mistral_conservative"
```

If operator `.env` contains `LLM_RATE_LIMIT_PROFILE=default`, battery **never** applies conservative profile. Diagnosis `runtime_settings` / config_pressure shows `default`.

**Effect:** RC-12 — aggressive retries until HOT; NDA tail hits **52** events.

---

### RC-12b — E4 advisory does not cover “default + concurrency 2” battery combo ⭐ P1

**Files:** `config_advisory.py` L113–124

E4 fires when `llm_global_concurrency > 3` without conservative profile. **Concurrency 2 + default** is the common battery misconfig and does **not** warn.

---

### RC-13a — Phase G has no “fast-but-wrong” health flag ⭐ P1

**Files:** `baseline_interpretation.py` L111–116, L173–236 · `atlassian_v1.json` L17 `review_wall_ms`

`funnel_story` = `"80 extracted → 34 evidence-compare → 8 LLM batches → 6 NC"` — no wall time, no compare_items for section-first contracts.

No flag when:

```text
review_wall_ms < baseline_wall_ms * 0.35
AND violations_nc < min_violations
```

**Effect:** RC-13 misread as PF-1 win.

---

### RC-13b — Battery does not fail on speed+accuracy anomaly ⭐ P1

**Files:** `run_live_contract_battery.py` · `validate_p5_golden.py`

Atlassian gate checks `min_violations` but not **minimum wall time** or **minimum compare_items** jointly with NC regression.

---

## 3. Fix tasks

### RC11-F1 — Cisco legal-score golden gate (IPC-5) ⭐ P0 · ~30 LOC

**Files:** `golden_thresholds.json`, `validate_p5_golden.py`, `run_live_contract_battery.py`

Add to `cisco` thresholds (P5-aligned floors, not aspirational 10.0):

```json
"cisco": {
  "max_section_ipc_pct": 85.0,
  "min_violations": 4,
  "min_legal_score_10": 6.0,
  "min_compare_items": 9,
  "min_section_compare_batches": 2,
  "min_section_score_hits": 4
}
```

New helper `_assert_cisco_legal_score(name, review, diagnosis)`:

- Compute score via existing `score_section_expected(by_section, CISCO_EXPECTED)`
- Fail LIVE/P5 battery when below `min_legal_score_10`
- Wire into `_assert_golden_gates` for `name == "cisco"`

**Acceptance:** LIVE score 5.0 fails; P5 score 6.7 passes.

**Note:** Keep `validate_p5_golden.run_cisco` aspirational message optional (`min_legal_score_10` from thresholds, not hardcoded 10.0).

---

### RC11-F2 — Cisco section compare funnel floors ⭐ P0 · ~20 LOC

**Files:** `validate_p5_golden.py`

```python
def _assert_section_compare_floors(name, diagnosis):
    thresholds = _load_thresholds().get(name) or {}
    min_items = thresholds.get("min_compare_items")
    min_batches = thresholds.get("min_section_compare_batches")
    section_pipeline = diagnosis.get("section_pipeline") or {}
    infra = (diagnosis.get("infrastructure") or {}).get("section_compare_batches") or {}
    compare_items = section_pipeline.get("compare_items") or diagnosis.get("compare_items")
    batches = infra.get("actual") or section_pipeline.get("llm_batches_actual")
    ...
```

**Acceptance:** Cisco LIVE with `compare_items=8` fails when floor is 9.

---

### RC11-F3 — Standalone Cisco runner + battery isolation ⭐ P1 · ~25 LOC

**Files:** new `temp_java_sync/run_cisco_review.py`, `run_live_contract_battery.py`

- `run_cisco_review.py` — single contract, golden defaults, full gates (mirror `run_atlassian_review.py`)
- Battery flag / env `BATTERY_SKIP_CISCO=1` for Atlassian-only debugging
- Document: **validate Cisco alone** before full battery when tuning quota

**Wall time:** Neutral — avoids cross-contract quota bleed when debugging; full battery unchanged when Cisco first + cooldown (B-RC F6).

---

### RC11-F4 — Section compare structure retry under HOT (mirror RC-10) ⭐ P1 · ~12 LOC

**Files:** `section_compare_llm.py`, `failure_policy.py` (reuse `stage` param)

Pass `stage="section_compare"` to `should_batch_single_retry` — exempt from HOT structure split cap (same rationale as obligation extract: recovery, not quota fan-out).

**Preserves:** Batch size unchanged; non-deterministic LLM preserved.

**Acceptance:** HOT + structure failure → single-section retry still runs for section compare.

---

### RC11-F5 — Optional `cisco_v1` baseline snapshot ⭐ P2 · ~40 LOC

**Files:** `review_agent/data/baselines/cisco_v1.json`, `temp_java_sync/baselines/cisco_v1.json`

Capture P5 reference: `legal_score_10=6.7`, `compare_items=10`, `llm_batches=3`, `review_wall_ms`, section hit count.

Wire `BASELINE_PROFILE=cisco_v1` for Cisco-only runs in `run_cisco_review.py`.

---

### RC12-F1 — Golden LLM profile in `apply_golden_review_defaults()` ⭐ P0 · ~10 LOC

**Files:** `bootstrap_env.py`, `run_atlassian_review.py`, `validate_p5_golden.py`, `run_cisco_review.py`

```python
def apply_golden_llm_profile_defaults() -> None:
    """RC-12 — battery/golden runs use conservative Mistral pacing unless opted out."""
    if os.environ.get("GOLDEN_LLM_PROFILE_OPT_OUT", "").strip().lower() in ("1", "true", "yes"):
        return
    os.environ.setdefault("LLM_RATE_LIMIT_PROFILE", "mistral_conservative")
```

Call from `apply_golden_review_defaults()`. Remove duplicate `if empty` block from `run_live_contract_battery.py` (single source).

**Behavior:** Explicit `.env` `default` still wins (`setdefault` only). For **forced** golden override add opt-in:

```python
if os.environ.get("GOLDEN_LLM_PROFILE_FORCE", "").strip().lower() in ("1", "true"):
    os.environ["LLM_RATE_LIMIT_PROFILE"] = "mistral_conservative"
```

Document in `.env.example`: battery scripts set `GOLDEN_LLM_PROFILE_FORCE=true` in harness entrypoints only.

**Acceptance:** Battery with `.env` default → diagnosis shows `mistral_conservative`, effective concurrency **1** when not explicitly overridden.

---

### RC12-F2 — Config advisory E10 (default profile on golden-scale battery) ⭐ P1 · ~15 LOC

**Files:** `config_advisory.py`

Warn when:

```python
settings.llm_rate_limit_profile == "default"
and settings.llm_global_concurrency >= 2
```

```text
rule_id=E10, severity=warn
message="LLM_RATE_LIMIT_PROFILE=default with LLM_GLOBAL_CONCURRENCY>=2 — use mistral_conservative for golden/battery runs"
```

**Acceptance:** `test_config_advisory.py::test_e10_default_profile_battery_posture`

---

### RC12-F3 — Surface resolved LLM profile in config_pressure ⭐ P1 · ~8 LOC

**Files:** `config_advisory.py` `build_config_pressure_diagnosis()`

Add fields:

```json
"llm_rate_limit_profile": "mistral_conservative",
"llm_global_concurrency_effective": 1
```

**Acceptance:** LIVE diagnosis `infrastructure.config_pressure` shows profile used for the run.

---

### RC12-F4 — Pre-flight profile check in battery ⭐ P1 · ~15 LOC

**Files:** `run_live_contract_battery.py`

After `load_env()` + golden defaults, log resolved profile and concurrency from `ReviewSettings()` once at startup; **fail fast** if `GOLDEN_LLM_PROFILE_FORCE` expected but profile still `default` (CI guard).

---

### RC13-F1 — Phase G flag `review_wall_time_suspicious` ⭐ P1 · ~25 LOC

**Files:** `baseline_interpretation.py`

When baseline has `review_wall_ms` and:

```python
review_wall_ms < baseline_wall_ms * 0.35
and violations_nc < baseline_min
and not pathological_ipc_funnel  # already flagged
```

Add health flag `review_wall_time_suspicious`.

Optional second flag `funnel_work_skipped` when:

```python
compare_items >= 15  # section path ran somewhat
and compare_queued < min_compare_queued * 0.5  # obligation funnel starved
and violations_nc < baseline_min
```

**Acceptance:** ATL LIVE 278s + NC 1 flags; P5 good run does not.

---

### RC13-F2 — Extend funnel_story with wall time + section compare ⭐ P2 · ~12 LOC

**Files:** `baseline_interpretation.py`

```python
def _build_funnel_story(actuals, *, compare_items=None, wall_min=None):
    base = f"{extracted} extracted → {queued} evidence-compare → {batches} LLM batches → {nc} NC"
    if compare_items is not None:
        base += f" | section_compare_items={compare_items}"
    if wall_min is not None:
        base += f" | wall={wall_min:.1f}min"
    return base
```

---

### RC13-F3 — Golden joint gate: NC + minimum work proxies ⭐ P1 · ~25 LOC

**Files:** `golden_thresholds.json`, `validate_p5_golden.py`

For `atlassian`:

```json
"min_review_wall_ms": 600000,
"max_wall_speed_ratio": 0.35
```

`_assert_wall_time_sanity(name, diagnosis, assessment)`:

- Only when `violations_nc >= min_violations` skip (already accurate)
- Fail when wall below floor **and** NC below min (double condition — avoids punishing fast good runs)

**Acceptance:** ATL LIVE 278s + NC 1 fails; good P5 passes.

---

### RC13-F4 — Export assessment speed anomaly hint ⭐ P2 · ~10 LOC

**Files:** `export_assessment.py`

When `baseline_interpretation.health_flags` contains `review_wall_time_suspicious`, add assessment field:

```json
"speed_anomaly": "wall_time_fast_vs_baseline_nc_low"
```

---

### RC-F11 — Tests ⭐ P0 · ~70 LOC

| Test file | Covers |
|-----------|--------|
| `temp_java_sync/tests/test_golden_cisco_score.py` | RC11-F1/F2 |
| `temp_java_sync/tests/test_bootstrap_llm_profile.py` | RC12-F1 |
| `tests/test_config_advisory.py` | RC12-F2 E10 |
| `tests/test_baseline_interpretation.py` | RC13-F1/F2 |
| `tests/test_failure_policy.py` | RC11-F4 section_compare stage |
| `temp_java_sync/tests/test_golden_wall_sanity.py` | RC13-F3 |

---

## 4. Implementation order

| Priority | Task | Fixes | LOC |
|----------|------|-------|-----|
| **P0** | RC12-F1 golden LLM profile defaults | RC-12 | 10 |
| **P0** | RC11-F1 Cisco legal-score gate | RC-11 · IPC-5 | 30 |
| **P0** | RC11-F2 section compare floors | RC-11 | 20 |
| **P0** | RC-F11 core tests | lock | 45 |
| **P1** | RC12-F2/F3/F4 profile observability | RC-12 · E | 38 |
| **P1** | RC11-F4 section compare HOT recovery | RC-11 · B | 12 |
| **P1** | RC11-F3 standalone Cisco runner | ops | 25 |
| **P1** | RC13-F1/F3 wall+NC anomaly flags/gates | RC-13 · G | 50 |
| **P2** | RC11-F5 cisco_v1 baseline | G | 40 |
| **P2** | RC13-F2/F4 funnel story + export | G | 22 |

**Total:** ~175 prod/harness, ~70 test.

**Validate after:** RC-080910 + B-RC on same agent restart.

---

## 5. What NOT to do

| Action | Why |
|--------|-----|
| Lower `section_compare_batch_size` or classify batch size | User constraint; does not fix 429 root cause |
| Force `temperature=0` or deterministic compare | User constraint |
| Disable section compare on 429 | Accuracy loss (RC-11 gets worse) |
| Disable Phase B posture globally | RC-01 retry spiral returns |
| Treat fast wall time as success without NC gate | RC-13 misread |
| Set Cisco legal gate to 10.0 when P5 reference is 6.7 | False failures on good P5 |
| Run full 5-contract battery in CI without cooldown between contracts | B-RC F6 exists — keep it |
| Increase concurrency to “finish faster” on 429 | RC-12 opposite fix |

---

## 6. Success metrics

| Metric | Cisco P5 ref | Cisco LIVE (bad) | Target |
|--------|--------------|------------------|--------|
| `legal_score_10` | 6.7 | 5.0 | **≥ 6.0** |
| `compare_items` | 10 | 8 | **≥ 9** |
| section `llm_batches` | 3 | 2 | **≥ 2** |
| `llm_rate_limit_profile` | conservative | default | **mistral_conservative** |
| `llm_rate_limit_events` (Cisco review) | low | elevated | **≤ 8** |

| Metric | Atlassian P5 | ATL LIVE (bad) | Target |
|--------|--------------|----------------|--------|
| `review_wall_ms` | ~1,680,000 | 278,000 | **≥ 600,000** when NC < 6, or NC **≥ 6** |
| `violations_nc` | 6 | 1 | **≥ 6** |
| Health flags | none | — | no `review_wall_time_suspicious` on good run |

**Wall time on successful runs:** May stay ~25 min for Atlassian (full funnel) — that is **correct**. Target is **not** to speed up; target is to **fail** runs that are fast because work was skipped.

---

## 7. Validation procedure

1. Set `GOLDEN_LLM_PROFILE_FORCE=true` in battery entry (or use fresh golden bootstrap).
2. **Cisco alone:** `python run_cisco_review.py` — score ≥ 6.0, compare_items ≥ 9, profile conservative.
3. **Full battery:** `python run_live_contract_battery.py` — Cisco first; cooldown if events ≥ 8; Atlassian NC ≥ 6.
4. Confirm diagnosis: no `review_wall_time_suspicious` on good Atlassian; LIVE bad artifact reproduces flag.
5. Regression:

```bash
cd Legal/review/review_agent
pytest tests/test_config_advisory.py tests/test_baseline_interpretation.py tests/test_failure_policy.py -q

cd Legal/temp_java_sync
pytest tests/test_golden_cisco_score.py tests/test_bootstrap_llm_profile.py tests/test_golden_wall_sanity.py -q
```

6. Optional: `python summarize_contract_runs.py` — LIVE Cisco row shows PASS on legal score.

---

## 8. Rollback

1. Remove `min_legal_score_10` / compare floors from `golden_thresholds.json`.
2. Clear `GOLDEN_LLM_PROFILE_FORCE` — operator `.env` controls profile again.
3. Remove E10 advisory rule.
4. Revert RC11-F4 `stage="section_compare"` exempt — HOT cap applies again.
5. Remove `review_wall_time_suspicious` from baseline interpretation.

---

## 9. Phase map

| Phase | Role |
|-------|------|
| **B / B-RC** | RC11-F4 section compare HOT recovery; RC-01 quota on section path |
| **E** | RC12-F2 E10; profile in config_pressure |
| **PF-1A** | RC12-F1 conservative profile for golden runs |
| **PF-1 (perf)** | RC13 — speed valid only when NC ≥ baseline |
| **G** | RC13-F1/F2 funnel story + wall flags |
| **IPC-5** | RC11-F1/F2 legal score + compare floors |
| **R8/R9** | Cisco routing golden unchanged (`wrong_policy_compare_count=0`); RC-11 is section-first |

---

## 10. Dependency graph

```text
RC-12 (profile=conservative, concurrency→1)
    └── ↓ 429 on Cisco section compare (RC-11)
            ├── RC11-F1/F2 golden legal score + compare floors
            └── RC11-F4 section compare structure recovery
RC-13 (wall time flags)
    └── catches ATL fast-fail even when partial gates pass
B-RC F6 cooldown ── still required between battery contracts
RC-080910 ── Atlassian NC path (orthogonal to Cisco section-first)
```

---

## One-line truth

**Cisco collapses because section compare loses batches to 429 under `default` profile; the battery looks fast because skipped compare and starved F5 finish early — fix golden profile enforcement, Cisco legal-score floors, section HOT recovery, and Phase G “fast-but-wrong” flags without shrinking batches.**
