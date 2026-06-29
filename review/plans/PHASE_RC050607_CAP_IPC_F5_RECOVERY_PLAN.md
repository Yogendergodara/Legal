# Phase RC-05/06/07 — Obligation Cap, False IPC Masking & F5 Recovery Starvation

**Version:** 1.0  
**ID:** `DR-PHASE-RC050607`  
**Parent:** [PHASE_RC0304_FUNNEL_TENANT_FIX_PLAN.md](./PHASE_RC0304_FUNNEL_TENANT_FIX_PLAN.md) · [PHASE_B_RC0102_QUOTA_POSTURE_FIX_PLAN.md](./PHASE_B_RC0102_QUOTA_POSTURE_FIX_PLAN.md) · [PHASE_G_ATLASSIAN_BASELINE_INTERPRETATION_PLAN.md](./PHASE_G_ATLASSIAN_BASELINE_INTERPRETATION_PLAN.md) · [PHASE_F_ACCURACY_PATHS_GUARD_PLAN.md](./PHASE_F_ACCURACY_PATHS_GUARD_PLAN.md)  
**Targets:** RC-05 (obligation cap not applied) · RC-06 (NC → IPC masking) · RC-07 (F5 `compare_omitted_recovered=0`)  
**Status:** **IMPLEMENTED**  
**Scope:** Restore P5 accuracy mechanisms without lowering F4 gates, without batch-size reduction, non-deterministic routing preserved, wall time not increased (target: ↓ wasted extract/planner work; tail recovery only where P5 already ran)  
**Effort:** ~0.5 engineering day (config/harness) + ~0.5 day (F5 routing + G flags) after RC-0304 validated  
**Risk:** Low (RC-05 harness-only); Medium for RC-07 tail routing (bounded by existing F5 caps)

---

## 1. Problem statement

After RC-03/04 (funnel + tenant), three **downstream** mechanisms still explain why Atlassian LIVE lost **5 of 6 NC** vs P5 — not because violations were deleted, but because **section-level NC was masked as IPC** and **F5 tail recovery did not fire**.

| RC | Symptom | Misread |
|----|---------|---------|
| **RC-05** | 117 obligations extracted, `cap_dropped=0` (P5: 80 capped, dropped=56) | “More obligations = better coverage” |
| **RC-06** | Sections 15, 19, 20.4: NC → IPC | “IPC is expected on hybrid path” |
| **RC-07** | `compare_omitted_recovered=0`, `gap_sections=1` (P5: 20 recovered, 21 gaps) | “F5 is optional polish” |

### Evidence tables

**RC-05 — obligation extract cap**

| Run | extracted | cap_dropped | max_obligations (effective) |
|-----|-----------|-------------|-------------------------------|
| ATL P5 (good) | 80 | 56 | **80** |
| ATL LIVE | 117 | 0 | **200** (default) |
| ATL rerun | 169 | 0 | **200** |

**RC-06 — section status regression (Atlassian)**

| Section | P5 | LIVE |
|---------|-----|------|
| 15 (indemnity) | NON_COMPLIANT | IPC |
| 19 (publicity) | NON_COMPLIANT | IPC |
| 20.4 (governing law) | NON_COMPLIANT | IPC |
| 13 | IPC | NC (wrong policy universe — RC-04) |

**RC-07 — F5 recovery funnel**

| Run | gap_sections | compare_omitted_recovered | unclear_recompared |
|-----|--------------|---------------------------|---------------------|
| ATL P5 | 21 | **20** | many |
| ATL LIVE | 1 | **0** | 1 |
| ATL rerun | 2 | 1 | 2 |

**Causal chain (ordered):**

```text
RC-04 tenant pollution + RC-03 compare_queued=0
  → wrong section/obligation IPC (RC-06)
  → almost no compare_omitted gaps tagged (RC-07)
RC-05 uncapped extract amplifies planner/retrieval noise before compare ever runs
B-RC quota starvation further suppresses F5 tail when funnel partially recovers
```

**Prerequisite:** [RC-0304 implemented](./PHASE_RC0304_FUNNEL_TENANT_FIX_PLAN.md) — RC-05/06/07 fixes are **additive**; RC-05 alone does not restore NC.

---

## 2. Code-proven root causes

### RC-05a — Golden harness uses default cap 200 ⭐ P0

**Files:** `config.py` L234 · `temp_java_sync/.env.example` L35 · `obligation_nodes.py` L165–185

Cap logic **exists and works** (EC-1 tests in `test_ec1_obligation_cap.py`). P5 warning proves it ran:

```text
obligation list capped to 80 (round_robin, dropped=56)
```

Battery / LIVE never set `MAX_OBLIGATIONS_PER_REVIEW=80`:

```python
max_obligations_per_review: int = 200  # default
```

**Effect:** +37–89 extra obligations → extra planner calls, catalog searches, retrieval — **before** compare queue (which was zero in LIVE). Wastes LLM budget and time without improving accuracy.

---

### RC-05b — No operator advisory when cap disabled on large contracts ⭐ P1

**Files:** `config_advisory.py` — E1–E7 exist; **no rule** for uncapped obligations on contracts with >15 reviewable sections.

---

### RC-06a — NC loss is **masked IPC**, not dedupe deletion ⭐ diagnostic

**Files:** `export_assessment.py` L47–58, L100–111 · `obligation_merge.py` L66–69

- `section_results()` rolls up **worst status per section** (NC beats IPC).
- `primary_findings()` gives `playbook_compare` priority over other sources **only for the same `section_id:dimension_label` key**.
- Obligation IPC uses `source=obligation_ipc` and obligation-scoped `dimension_label` — **different key** from section compare rows.

**Conclusion:** Sections 15/19/20.4 show IPC because **section compare never produced grounded NC** (wrong catalog / no compare / IPC from coverage gate), not because merge deleted NC.

**Primary mechanism:** RC-03/04 → section compare returns IPC or is skipped → no violation row.

---

### RC-06b — `ipc_fallback` cutover does not rescue when section compare returns IPC items ⭐ P1

**Files:** `section_compare_nodes.py` L120–144, L137–143

`obligation_section_cutover_mode=ipc_fallback` re-includes obligation-covered sections for section compare **only when all obligation findings are IPC/inconclusive**. That ran in LIVE — but section compare with **wrong hits** still yields IPC, not NC.

**Fix layer:** Restore correct policy scope (RC-0304) first; then optional **status guard** so grounded `playbook_compare` NC is never superseded by later obligation-only IPC rows in the same section (see RC6-F2).

---

### RC-07a — F5 Phase 1b only runs `compare_omitted` gap type ⭐ P0

**Files:** `final_verify_llm.py` L551–605 · `section_merge.py` L129–132, L247–251

`compare_omitted_recovered` increments only when:

1. Section is in `compare_omitted_gap_ids` (`gap_type=compare_omitted`), **and**
2. Section bundle has `policy_hits`, **and**
3. Re-compare returns items.

`compare_omitted` gaps are created only when section **had hits but was not in `compare_items`** (`findings_for_no_policy_sections`). If section compare **ran** and returned IPC (wrong policy), section is **not** compare_omitted → F5 Phase 1b **skips** it.

P5’s 20 recoveries = sections with hits where batch compare **omitted** the section entirely.

LIVE `gap_sections=1` → F5 had almost nothing to recover.

---

### RC-07b — Obligation IPC rows ineligible for unclear re-compare ⭐ P0

**Files:** `unclear_recompare.py` L58–100, L103–118 · `obligation_merge.py` L66–69

Obligation IPC findings:

- `source=obligation_ipc` (no `gap_type`)
- `classify_unclear_finding` → **`inconclusive_other`**
- `eligible_for_unclear_recompare` → **False**

LIVE logs: ~23 unclear findings marked **ineligible** — these are obligation routing IPC rows (`routing_or_skip`), not F5 gap types.

**Effect:** F5 Phase 3 (unclear recompare) cannot surface NC from obligation-only IPC even when section bundle has good hits.

---

### RC-07c — Quota headroom starves tail when funnel partially works ⭐ P1

**Files:** B-RC plan · `final_verify_llm.py` (same LLM semaphore as compare)

Even after RC-0304, if compare batches consume quota, F5 tail batches may 429. B-RC (review-scoped reset, conservative profile, battery cooldown) is **prerequisite** for RC-07 validation.

---

## 3. Mechanism (ASCII)

```text
[P5 GOOD]
  cap 80 → 42 compare_queued → section compare + 21 gap_sections
       → F5 compare_omitted_recovered=20 → NC on 19, 20.4

[LIVE BAD]
  cap 200 → 117 obligations → 0 compare_omitted gaps (wrong IPC, not omitted)
       → obligation_ipc × N → inconclusive_other (F5 ineligible)
       → gap_sections=1 → compare_omitted_recovered=0
       → sections 15/19/20.4 stay IPC
```

---

## 4. Fix strategy (minimal, production-grade)

| Layer | Phase | Fixes | Wall time |
|-------|-------|-------|-----------|
| **A — Config / harness** | IPC-0 · EC-1 · E | RC-05 | ↓ (less extract/planner) |
| **B — F5 gap routing** | F · IPC-4 | RC-07 | ↑ tail only (bounded); net ↓ vs full re-review |
| **C — Status / interpretation** | F · G | RC-06 observability + NC guard | Neutral |
| **D — Upstream (done)** | RC-0304 | RC-03/04 funnel | ↓ bad catalog searches |

**Constraints honored:**

- No batch size reduction
- No F4 evidence gate lowering
- Non-deterministic routing preserved
- F5 caps unchanged (`final_verify_unclear_recompare_max_sections`, adaptive mode)

---

## 5. Implementation tasks

### RC5-F1 — Golden `MAX_OBLIGATIONS_PER_REVIEW=80` ⭐ P0 · ~12 LOC

**Files:** `temp_java_sync/.env.example`, `run_live_contract_battery.py`, `run_atlassian_review.py` (via bootstrap)

```python
# run_live_contract_battery.py — after load_env()
if os.environ.get("MAX_OBLIGATIONS_PER_REVIEW", "").strip() == "":
    os.environ["MAX_OBLIGATIONS_PER_REVIEW"] = "80"
```

Mirror P5. Cap logic already in `obligation_nodes.py` — **no graph change**.

**Acceptance:** Atlassian review warning contains `obligation list capped to 80`; `obligation_cap_dropped_count` > 0 when extract > 80.

---

### RC5-F2 — Config advisory E8 (uncapped large contract) ⭐ P1 · ~18 LOC

**Files:** `config_advisory.py`

New rule when:

```python
settings.max_obligations_per_review > 80
and reviewable_sections >= 15
```

Message: recommend `MAX_OBLIGATIONS_PER_REVIEW=80` for golden-scale contracts.

Surfaces in preflight warnings + `engine_diagnosis.infrastructure.config_pressure.advisories`.

---

### RC5-F3 — Golden preflight for extract cap ⭐ P1 · ~25 LOC

**Files:** `golden_thresholds.json`, `validate_p5_golden.py`

```json
"atlassian": {
  "max_obligations_extracted": 85,
  "min_obligation_cap_dropped": 1
}
```

Only enforce `min_obligation_cap_dropped` when raw extract (pre-cap) would exceed 80 — read from `engine_diagnosis.obligation_pipeline.extract_cap` or compliance_stats.

**Acceptance:** LIVE-style 117 extract with `cap_dropped=0` fails fast.

---

### RC5-F4 — Always surface extract_cap in diagnosis ⭐ P2 · ~8 LOC

**Files:** `engine_diagnosis.py`

Emit `obligation_pipeline.extract_cap` whenever `obligation_cap_dropped_count >= 0` and extract ran (even if dropped=0), so operators see cap mode vs uncapped.

---

### RC7-F1 — Promote “hits + obligation-only IPC” sections to `compare_omitted` ⭐ P0 · ~35 LOC

**Files:** `section_merge.py` or new helper `recovery_gap_candidates.py`, wired from `merge_section_findings_node`

After merge, for each section where:

- `bundles[sid].policy_hits` non-empty, **and**
- No `playbook_compare` item with status NC/COMPLIANT for that section, **and**
- All section-scoped findings are IPC/inconclusive (obligation + section), **and**
- Section not already in `compare_omitted_gap_ids`

→ Append `sid` to `compare_omitted_gap_ids` and `gap_section_ids` (idempotent).

**Rationale:** Gives F5 Phase 1b the same hook P5 used when batch compare omitted sections — **without** re-running full section compare for every section upfront.

**Wall time:** Only sections F5 would already touch (cap-limited); uses existing batch sizes.

**Acceptance:** Unit test — bundle has hits, compare_items empty, obligation IPC only → section in `compare_omitted_gap_ids`.

---

### RC7-F2 — Optional: `obligation_evidence_ipc` unclear reason for F5 Phase 3 ⭐ P1 · ~30 LOC

**Files:** `unclear_recompare.py`, `obligation_merge.py`

When `source=obligation_ipc` **and** section bundle has hits **and** `routing_audit.evidence.decision=ipc` with reason ≠ `routing_or_skip` on empty fence:

- Classify as `obligation_evidence_ipc` (new reason in `_RECOMPARE_REASONS`)
- Eligible when `_has_policy_context(finding)` or bundle hits exist

**Scope limit:** Do **not** enable recompare for pure `routing_or_skip` (no candidates) — that is intentional IPC.

**Acceptance:** Finding with evidence IPC + hits → eligible; pure routing_or_skip → still ineligible.

---

### RC7-F3 — Golden recovery floors (IPC-5) ⭐ P1 · ~20 LOC

**Files:** `golden_thresholds.json`, `validate_p5_golden.py`

```json
"atlassian": {
  "min_compare_omitted_recovered": 12,
  "min_gap_sections": 10
}
```

Read from `engine_diagnosis.recovery.gap_status_summary` or `accuracy_paths.recover`.

**Acceptance:** P5 artifact passes; LIVE bad artifact fails.

---

### RC6-F1 — Phase G: `section_nc_regression` vs `ipc_expected_high` ⭐ P1 · ~40 LOC

**Files:** `baseline_interpretation.py`, optional `baselines/atlassian_v1.json` section map

When `violations_nc < baseline_min` **and** `pathological_ipc_funnel` **not** set **and** `obligation_ipc_rate >= 0.85`:

- Flag `section_nc_regression` (accuracy mechanism)
- Do **not** conflate with `ipc_expected_high` alone

Optional: store P5 reference section NC ids in baseline JSON for delta reporting (sections 15, 19, 20.4).

**Acceptance:** LIVE reproduces `section_nc_regression`; good P5 does not.

---

### RC6-F2 — NC preservation in section rollup (minimal guard) ⭐ P2 · ~25 LOC

**Files:** `export_assessment.py` `section_results()` **or** `merge_section_findings_node`

When rolling up per section, if any `playbook_compare` or `section_first_final` finding has grounded NC, **do not** let `obligation_ipc` alone determine section status for assessment export.

Implementation: in `section_results`, prefer worst status among findings where `source in (playbook_compare, section_first_final, obligation_compare)` before obligation_ipc-only rows.

**Note:** This is **reporting guard** only — does not fake NC. Primary fix remains RC-0304 + RC7-F1.

---

### RC6-F3 — Accuracy paths: recovery starvation flag ⭐ P2 · ~15 LOC

**Files:** `accuracy_paths.py`, `baseline_interpretation.py`

Flag `f5_recovery_starved` when:

```python
gap_sections >= 5 and compare_omitted_recovered == 0 and compare_omitted_eligible > 0
```

(eligible = len(compare_omitted_gap_ids) from stats)

---

### RC-F9 — Tests ⭐ P0 · ~80 LOC

| Test file | Covers |
|-----------|--------|
| `tests/test_ec1_obligation_cap.py` | extend — cap at 80 integration via obligation_extract_node |
| `tests/test_recovery_gap_candidates.py` | RC7-F1 |
| `tests/test_unclear_recompare.py` | RC7-F2 eligibility |
| `tests/test_baseline_interpretation.py` | RC6-F1, RC6-F3 |
| `temp_java_sync/tests/test_golden_recovery_thresholds.py` | RC7-F3 |

---

## 6. Implementation order

| Priority | Task | Fixes | LOC |
|----------|------|-------|-----|
| **P0** | RC5-F1 golden cap 80 | RC-05 | 12 |
| **P0** | RC7-F1 compare_omitted promotion | RC-07 | 35 |
| **P0** | RC-F9 core tests | lock | 50 |
| **P1** | RC5-F2 E8 advisory | RC-05 · E | 18 |
| **P1** | RC5-F3 golden extract cap | IPC-5 | 25 |
| **P1** | RC7-F2 obligation_evidence_ipc | RC-07 | 30 |
| **P1** | RC7-F3 recovery golden floors | IPC-5 | 20 |
| **P1** | RC6-F1 section_nc_regression | G | 40 |
| **P2** | RC5-F4 · RC6-F2 · RC6-F3 | ops | 48 |

**Total:** ~130 prod/harness, ~80 test.

**Validate only after:** RC-0304 + B-RC on same run.

---

## 7. What NOT to do

| Action | Why |
|--------|-----|
| Lower F4 evidence gates to force compare | False NC / wrong-policy (Phase F forbids) |
| Disable obligation cap globally | RC-05 returns; planner noise |
| Disable `ipc_fallback` / run full duplicate section+obligation compare everywhere | Wall time ↑↑ |
| Shrink batch sizes | User constraint |
| Recompare all `routing_or_skip` obligation IPC | No candidates — wasted LLM |
| Set `FINAL_GAP_VERIFY_ENABLED=false` | Removes P5 safety net |
| Treat `ipc_expected_high` as success when `violations_nc` dropped | RC-06 misread |

---

## 8. Success metrics (Atlassian golden, after RC-0304 + this plan)

| Metric | P5 baseline | LIVE (bad) | Target |
|--------|-------------|------------|--------|
| `obligations_extracted` (post-cap) | 80 | 117 | **≤ 85** |
| `obligation_cap_dropped_count` | 56 | 0 | **≥ 1** (when raw > 80) |
| `compare_omitted_recovered` | 20 | 0 | **≥ 12** |
| `gap_sections` | 21 | 1 | **≥ 10** |
| Section 15 / 19 / 20.4 status | NC | IPC | **NC** |
| NON_COMPLIANT (total) | 6 | 1 | **≥ 6** |
| Review wall time | ~28 min | ~4.6 min* | **≤ 28 min** |

\*Short wall time with zero compare + zero F5 recovery is **failure**, not optimization.

---

## 9. Validation procedure

1. Confirm RC-0304 + B-RC deployed.
2. Set `MAX_OBLIGATIONS_PER_REVIEW=80` (RC5-F1).
3. Single Atlassian: `python run_atlassian_review.py` — check cap warning, `compare_omitted_recovered`, sections 15/19/20.4.
4. Full battery: `python run_live_contract_battery.py`.
5. Regression: `pytest tests/test_recovery_gap_candidates.py tests/test_baseline_interpretation.py tests/test_ec1_obligation_cap.py -q`
6. Phase G: `BASELINE_PROFILE=atlassian_v1` — no `section_nc_regression` or `pathological_ipc_funnel` on good run.

---

## 10. Rollback

1. Remove `MAX_OBLIGATIONS_PER_REVIEW=80` harness default (restore 200).
2. Feature flag `recovery_promote_obligation_ipc_gaps=false` — disable RC7-F1 promotion.
3. Remove golden recovery floors from `golden_thresholds.json`.
4. RC7-F2: remove `obligation_evidence_ipc` from `_RECOMPARE_REASONS`.

---

## 11. Phase map

| Phase | Role |
|-------|------|
| **IPC-0 / EC-1** | RC5-F1–F4 — cap at 80, advisory |
| **E** | RC5-F2 — operator warn on uncapped large contracts |
| **F (F4/F5)** | RC7-F1/F2 — tail recovery routing; RC6-F2 NC guard |
| **G** | RC6-F1/F3 — `section_nc_regression`, recovery starvation flags |
| **IPC-4** | RC7-F1/F2 — obligation IPC → F5 paths |
| **IPC-5** | RC5-F3, RC7-F3 — golden floors |
| **B-RC** | Quota headroom for F5 tail |
| **RC-0304** | **Prerequisite** — without funnel, RC-05/06/07 fixes cannot restore NC |

---

## 12. Dependency graph

```text
RC-0304 (tenant + compare_queued)
    ├── RC-05 (cap 80) ──────────── ↓ planner noise
    ├── RC-07-F1 (F5 gaps) ──────── ↑ compare_omitted_recovered
    │       └── B-RC (quota) ────── tail LLM headroom
    └── RC-06 (NC visible) ──────── ↑ violations_nc
            └── RC6-F1 (G flags) ── operator clarity
```

---

## One-line truth

**RC-05 wastes budget upstream; RC-06/07 lose NC downstream because F5 only recovers `compare_omitted` gaps — fix cap + promote hit-backed IPC sections into F5’s existing recovery path, after RC-0304 restores the compare funnel.**
