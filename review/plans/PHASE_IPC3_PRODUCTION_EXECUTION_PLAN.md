# Phase IPC-3 — Production IPC Recovery (Execution Plan)

**Version:** 1.4  
**ID:** `DR-PHASE-IPC3`  
**Parent:** [PHASE_PR01_PRECISION_FUNNEL_RECOVERY_PLAN.md](./PHASE_PR01_PRECISION_FUNNEL_RECOVERY_PLAN.md) · [PHASE_OB02B_PR02B_POLICY_TAGGER_QUALITY_PLAN.md](./PHASE_OB02B_PR02B_POLICY_TAGGER_QUALITY_PLAN.md) · [PHASE_MCP_GLOBAL_CONCURRENCY_PLAN.md](./PHASE_MCP_GLOBAL_CONCURRENCY_PLAN.md)  
**Baseline artifact:** `temp_java_sync/outputs/atlassian_ipc3_baseline.json` (freeze at IPC3-0 — do not use a single ad-hoc smoke as sole reference)  
**Status:** **IN PROGRESS** — IPC0-R **implemented**; IPC3-0E band **measured**; **E-IDX next**  

---

## 0.1 Measured variance band (IPC3-0E — authoritative)

Source: `temp_java_sync/outputs/ipc3_variance_summary.json` (3 runs, v1 prompt, flags off).

| Metric | min | median | max | Old single baseline |
|--------|-----|--------|-----|---------------------|
| `obligation_ipc_rate` | 0.845 | **0.887** | 0.971 | 0.773 |
| `post_validation_compared` | 2 | **8** | 11 | 15 |
| `compare_queued` | 24 | **27** | 29 | 29 |
| `PRE_IPC` | 60 | **63** | 69 | 37 |
| `routing_or_skip` | 16 | **19** | 22 | 14 |
| `llm_rate_limit_events` | 6 | **6** | 11 | 8 |
| `nc_violations` | 0 | **0** | 2 | 0 |
| Wall time | ~15.5 min | **~16 min** | ~16 min | ~16 min |

**Judgment rule:** E-IDX success = `PRE_IPC` ↓ or `QUEUED` ↑ vs **median** (not vs 0.773). Old 0.773 was a favorable single run.

**IPC0-R code (shipped):** `compare_prompt_loader.py`, `ipc3_gates.py`, env flags, `ipc3_funnel_check.py`, NC quote_validate fix.

---

## 0. Start here — mandatory order (do not skip)

Nothing in this plan is judged until these complete **in this order**:

```text
Step 0  IPC0-R   Recover frozen runtime config (prompt v1 + flags off)     ← FIRST
Step 1  IPC3-0E  3× smoke → measured variance band (not §1.3 placeholders)
Step 2  IPC3-0A–D  Freeze baseline artifact + audit export (can overlap 0E)
Step 3  E-INF1   429-clean keys (if not already)
Step 4  E-IDX    MCP restart + re-sync (only after band exists)
Step 5  E-BP / E-RT2 / E-EV / E-LLM1  one flag per smoke
```

**Do not jump to E-IDX after a bad smoke** — that repeats the single-point comparison mistake.

### IPC0-R — Recover frozen runtime (blocking, before IPC3-0E)

**Confirmed repo state (2026-07):** E-LLM1 **overwrote** `obligation_compare.md` **in place** (commit `d8f1c0e`). There is **no** `OBLIGATION_COMPARE_PROMPT_V2` flag in code yet — loader always reads the current file (`obligation_compare_llm.py` → `prompts/obligation_compare.md`).

| Item | Current state | Frozen config for variance band |
|------|---------------|----------------------------------|
| Obligation compare prompt | **v2 live** (batch rules, IPC/INCONCLUSIVE split) | **v1** from git `825575a` |
| `IPC3_BOILERPLATE_SUBSTANTIVE_OVERRIDE_ENABLED` | `false` | `false` |
| `EVIDENCE_SEMANTIC_OVERLAP_ENABLED` | `false` | `false` |
| E-INF1 key pool | **on** in `review_agent/.env` | **on** (document; isolate 429 separately) |

**IPC0-R1 — Prompt recovery (required):**

**Verify parent commit (run before restore):**

```powershell
git log --oneline -5 -- review/review_agent/review_agent/prompts/obligation_compare.md
# Expect: d8f1c0e (v2 overwrite) then 825575a (last v1) — only these two commits touch this file.
# Use the commit *immediately below* the overwrite as V1_REV (currently 825575a).
```

Confirmed 2026-07: for this file, `825575a` is the sole parent revision before `d8f1c0e` (not a distant ancestor).

```powershell
cd d:\Ankit_legal\Legal
$V1_REV = "825575a"   # re-confirm via git log above before each restore
git show "${V1_REV}:review/review_agent/review_agent/prompts/obligation_compare.md" `
  > review/review_agent/review_agent/prompts/obligation_compare_v1.md
# Keep current content as v2:
Copy-Item review/review_agent/review_agent/prompts/obligation_compare.md `
  review/review_agent/review_agent/prompts/obligation_compare_v2.md
# Restore v1 as the active prompt until E-LLM1 experiment:
git show "${V1_REV}:review/review_agent/review_agent/prompts/obligation_compare.md" `
  > review/review_agent/review_agent/prompts/obligation_compare.md
```

**IPC0-R2 — Loader flag (code, same PR as above):**

- Add `OBLIGATION_COMPARE_PROMPT_V2_ENABLED=false` (default **false**)
- Loader: `v1` when false, `v2` when true — never overwrite `obligation_compare.md` again

**IPC0-R pass:** `git diff` shows v1 active; v1 and v2 files both exist; flag default false.

**IPC-NC1 — Empty-quote NC (parallel, not blocked on band):**

Post-smoke finding `9:6` Refund Policy — `NON_COMPLIANT` with `contract_quote=""`, `downgrade_source=quote_validate`, status **preserved** as NC. Investigate **before** treating NC count as noise:

| Check | Action |
|-------|--------|
| IPC-NC1A | File ticket: `outputs/ipc3_nc_empty_quote.json` with finding_id `e36b227e-9190-41a2-89d8-6f749d23d799` |
| IPC-NC1B | Trace `quote_validate` / `grounding` — NC must not survive with empty `contract_quote` (downgrade to INCONCLUSIVE or IPC) |
| IPC-NC1C | Re-run section 9 only after fix — independent of variance band |

This is a **grounding bug candidate**, not IPC-rate noise.

---

## 1. Executive summary

### 1.1 Critical rule — code merged ≠ plan executed

**Lesson (post-implementation smoke, 2026-07):** Merging IPC-3 code + running one smoke **without E-IDX** produced **worse** IPC (0.773 → 0.818). That is **expected** under this plan, not a plan failure — but the plan previously allowed confusing **implementation** with **activation**.

| What landed | Active at smoke? | Moves IPC? |
|-------------|------------------|------------|
| IPC3-0 telemetry / funnel check | Yes | **No** |
| E-BP2 boilerplate override | **No** (`…_ENABLED=false`) | No |
| E-EV1 semantic overlap | **No** (`…_ENABLED=false`) | No |
| **E-LLM1** compare prompt | **Yes** (prompt file live) | L2 only — small |
| **E-INF1** key pool | **Yes** | Section / timing |
| **E-IDX** re-sync | **No** | **L1 fix — missing** |

**Regression driver:** `routing_or_skip` 14 → **21** (+7) → `PRE_IPC` 37 → **43**. Main fixes for that bucket were **not applied**. Remainder = `parallel_hybrid` run variance.

**Do not call a smoke "post-IPC3 success/failure" until E-IDX completes.** Until then, only compare to **variance band** (§1.3).

### 1.2 Problem statement (unchanged)

Atlassian obligation IPC is **not one bug**. It is a **partitioned funnel** with three independent IPC layers. Fixes must be **measured one experiment at a time** with **paired precision metrics** — not IPC count alone.

**Principle (production norm):** *Broad retrieval inside tenant fence → compare LLM is the precision layer.* Pre-compare gates block **noise** (boilerplate, empty fence, incompatible family), not **paraphrase mismatch**.

**Primary target:**

| Metric | Baseline (smoke) | Gate |
|--------|------------------|------|
| `obligation_ipc_rate` | **0.773** (51/66) | **< 0.50** |
| `post_validation_compared` | **15** | **≥ 20** |
| `compare_queued` | **29** | **≥ 35** |
| `wrong_policy_blocked` | **0** | **0** (no regression) |
| Atlassian NC (429-clean run) | **0** | **≥ 4** (battery reference) |

**Secondary (section path, 429-sensitive):**

| Metric | Baseline | Gate |
|--------|----------|------|
| `section_ipc_pct` | 88.9% | < 50% (429-clean) |
| `llm_rate_limit_events` | 8 | 0 on validation run |
| `section_compare_failed` findings | 4 | 0 |

### 1.3 Variance band (required before declaring regression)

`parallel_hybrid` + hybrid retrieval + LLM planner are **not deterministic**. A single before/after smoke **will lie**.

**IPC3-0E (blocking — runs after IPC0-R, before E-IDX or any IPC judgment):**

| Step | Action |
|------|--------|
| IPC3-0E0 | Complete **IPC0-R** (v1 prompt active, BP2/EV1 flags off) |
| IPC3-0E1 | Run **3×** `run_pr01_atlassian_smoke.py` on **identical** frozen config |
| IPC3-0E2 | Write `outputs/ipc3_variance_summary.json` with **min / max / median** per layer metric |
| IPC3-0E3 | Replace §1.3 table below with **measured** values — until then, ignore the illustrative row |

**Illustrative only (2 data points — NOT a calibrated band):**

| Metric | Placeholder (do not use for decisions) |
|--------|----------------------------------------|
| `PRE_IPC` | saw 37 and 43 in two runs |
| `routing_or_skip` | saw 14 and 21 |
| `obligation_ipc_rate` | saw 0.773 and 0.818 |

**After IPC3-0E2, §1.3 table must be replaced with:**

```json
{
  "runs": ["ipc3_variance_run_1.json", "ipc3_variance_run_2.json", "ipc3_variance_run_3.json"],
  "PRE_IPC": {"min": null, "max": null, "median": null},
  "compare_queued": {"min": null, "max": null, "median": null},
  "obligation_ipc_rate": {"min": null, "max": null, "median": null}
}
```

**Regression declaration rule:** New smoke is a **regression** only if:

1. Same bucket schema version, **and**
2. Metric outside **measured** 3-run min/max band (not illustrative), **and**
3. The **activated experiment** explains the bucket (e.g. E-BP2 on → boilerplate should ↓).

Otherwise label: **VARIANCE — hold** (do not tune gates).

### 1.4 Release tiers (code ship vs experiment activate)

| Tier | Contents | Default flags | When to smoke |
|------|----------|---------------|---------------|
| **R0** | Tests, funnel check, audit export | BP2/EV1 off, **prompt v1** | Only for **IPC3-0E** band — **no IPC judgment** |
| **R1** | E-INF1 keys | pool on | 429 metrics only |
| **R2** | **E-IDX** re-sync | N/A | **First IPC judgment** vs variance band |
| **R3** | E-BP2 / E-EV1 / E-RT2 | one flag per experiment | One per smoke |
| **R4** | E-LLM1 prompt v2 | `OBLIGATION_COMPARE_PROMPT_V2_ENABLED=false` | Isolated A/B only |

**Already-merged state (failed run):** v2 prompt was live without flag; **IPC0-R reverses this** before any new smoke.

**Forbidden:** Ship R0+R4 in one merge; run E-IDX before IPC3-0E; use §1.3 placeholder as band.

---

## 2. Canonical funnel (definitions — must not change between runs)

Every obligation review produces **exactly one** evidence label in `skip_by_reason` (sums to `extracted`).

### 2.0 Arithmetic vs semantics (read before comparing runs)

§2.1 identities catch **arithmetic errors** only. They do **not** guarantee that a bucket label means the same thing after code changes:

| Experiment | Buckets whose **semantics** change |
|------------|----------------------------------|
| E-BP2 | `boilerplate` — obligations with override may leave this bucket |
| E-EV1 | `low_concept_overlap`, `evidence_sufficient` — semantic pass adds compare path |
| E-RT2 | `routing_or_skip` — marginal catalog hits may move to expand/compare |

**Rule:** After any experiment that touches `obligation_retrieval.py`, `catalog_matcher.py`, or `evidence_sufficiency.py`, record in the pass template (§5.3):

1. Which buckets changed definition (table above).
2. Compare **layer totals** (`PRE_IPC`, `QUEUED`, `llm_ipc_count`) and **paired precision** — not raw bucket counts vs baseline, unless semantics unchanged.

Example: post E-BP2, `boilerplate=12` vs baseline `17` is meaningful; post E-BP2, baseline `boilerplate=17` is **not** comparable to a re-run on old code.

### 2.1 Identity equations (arithmetic — every smoke JSON)
```text
N = obligation_count = extracted                                    [66]

PRE_IPC = count(skip_by_reason[k]) for all k ≠ "evidence_sufficient" [37]
QUEUED  = skip_by_reason["evidence_sufficient"] = compare_queued      [29]

Identity checks (required on every smoke JSON):
  N = PRE_IPC + QUEUED                                              ✓ 66 = 37 + 29
  QUEUED = llm_items_returned                                       ✓ 29 = 29
  QUEUED = llm_ipc_count + post_validation_compared                 ✓ 29 = 14 + 15
  N = PRE_IPC + llm_ipc_count + post_validation_compared            ✓ 66 = 37 + 14 + 15
  obligation_ipc_findings = PRE_IPC + llm_ipc_count                 ✓ 51 = 37 + 14
```

### 2.2 Layer map (do not conflate)

| Layer | Count | Mechanism | Is compliance verdict? |
|-------|-------|-----------|----------------------|
| **L1 Pre-LLM IPC** | 37 | Deterministic rules + retrieval scores | **No** — `INSUFFICIENT_POLICY_CONTEXT`, empty quotes |
| **L2 Compare LLM IPC** | 14 | LLM returned IPC after seeing policy chunks | **No** — “can’t decide” |
| **L3 Compared verdict** | 15 | LLM returned COMPLIANT / INCONCLUSIVE / NC | **Yes** — only layer with grounded compare |

**Finding source reconciliation (obligation path only):**

```text
obligation_ipc findings:     37  (L1)
obligation_compare findings: 14  (L2 — LLM IPC)
(null source) verdicts:        15  (L3)
Total obligation findings:     66
```

Section path adds separate findings (`playbook_compare`, `section_compare_failed`, `coverage_gate`) — **do not mix into obligation funnel denominators**.

### 2.3 Pre-IPC bucket breakdown (baseline only — semantics frozen at IPC3-0)

| `skip_by_reason` | Count | Root-cause ID |
|------------------|-------|---------------|
| `boilerplate` | 17 | **RC-BP** |
| `routing_or_skip` | 14 | **RC-RT** |
| `low_relevance_score` | 3 | **RC-EV** |
| `low_concept_overlap` | 2 | **RC-EV** |
| `insufficient_evidence` | 1 | **RC-EV** |
| `evidence_sufficient` | 29 | *(queued — not IPC)* |

---

## 3. Root-cause registry (code-proven)

### RC-BP — Boilerplate pre-IPC (17) — largest pre-LLM bucket

| Item | Detail |
|------|--------|
| **Where** | `obligation_boilerplate.py` · `obligation_extract.py` L77–87 · `catalog_matcher.py` L106–112 · `obligation_retrieval.py` L308–314 · `semantic_routing_planner.py` |
| **Mechanism** | `routing_source=skipped_boilerplate` → `skipped_reason=boilerplate` → `evidence.decision=ipc` without retrieval |
| **Baseline signal** | All 17 show `Routing confidence=0.00, source=skipped_boilerplate` in smoke rationales |
| **Risk** | False skip of substantive obligations in definitional-looking sections |
| **Do NOT** | Disable boilerplate globally |

**Production fix:** Audit-first, then rule-based override (PR-06 — partial code exists).

---

### RC-RT — Routing / catalog skip (14)

| Item | Detail |
|------|--------|
| **Where** | `catalog_matcher.py` L115–124, L193–204 · `obligation_retrieval.py` L316–329 · `evidence_sufficiency.py` L147–161 |
| **Mechanism** | Low planner confidence + no explicit mentions → `route_decision=ipc`; or `ipc_preflight` when no candidates inside fence |
| **Index coupling** | Weak AUP tags (`weak_tag_count=1`) reduce category-aligned retrieval → more catalog misses |
| **Risk** | Loosening `catalog_match_min_score` without index fix admits wrong-policy candidates |

**Production fix:** OB-02B index quality **first**; catalog threshold change **only as measured experiment E-RT2** after E-IDX passes.

---

### RC-EV — Evidence gates (6)

| Item | Detail |
|------|--------|
| **Where** | `evidence_sufficiency.py` `concept_overlap_score` L21–39 · `_hits_pass_gates` L88–110 |
| **Mechanism** | Token Jaccard + `evidence_min_score` (0.35) + `evidence_min_concept_overlap` (0.15); expand round exhausted |
| **Shipped mitigation** | `EVIDENCE_RERANK_BYPASS_ENABLED=true` (half-overlap + rerank) — baseline still has 6 failures |
| **Risk** | Lowering thresholds ↑ recall, ↓ precision (false compares / false NC) |

**Production fix:** PR-04B semantic overlap (precision-safe) before any threshold knob (E-EV2 last resort).

---

### RC-LLM — Post-compare LLM IPC (14)

| Item | Detail |
|------|--------|
| **Where** | `obligation_compare_llm.py` · prompt `obligation_compare.md` |
| **Mechanism** | All 29 queued obligations reached LLM (`llm_items_returned=29`); 14 returned `INSUFFICIENT_POLICY_CONTEXT` |
| **Not caused by** | 429 on obligation batches (`llm_batches_failed=0`) |
| **Risk** | Prompt/context too thin; wrong policy passages despite retrieval |

**Production fix:** Richer index (RC-RT), compare context envelope (PR-07), obligation compare prompt tuning — **after** L1 funnel improves.

---

### RC-429 — Section path (parallel track)

| Item | Detail |
|------|--------|
| **Where** | `section_compare_llm.py` · `llm_gateway.py` |
| **Mechanism** | Mistral 429 → `section_compare_failed` (4 findings); does **not** reduce `compare_queued` |
| **Fix** | Paid tier + valid keys in **both** `.env` files; `LLM_KEY_POOL_ENABLED=true` with 3 real keys |

---

## 4. What is already shipped (do not re-implement)

| ID | Status | Effect on baseline |
|----|--------|-------------------|
| OB-01 | DONE | `obligation_retrieval_section_skip_count=0` |
| OB-03 | DONE | `routing_validation_rejected=0` |
| OB-04 / PR-04A | DONE | Rerank bypass env live |
| PR-05 | DONE | Expand-first catalog, `CATALOG_MATCH_MAX_CANDIDATES=8` |
| PR-06A/B | DONE | Explicit mention confidence floor 0.55 |
| PR-07 | DONE | `OBLIGATION_COMPARE_MAX_OBLIGATION_CHARS=3000` |
| MCP semaphore | DONE | `breaker_open_events_mcp=0` |
| OB-02B code | DONE | Tagger prompt/sanitize/prior — **index not refreshed on smoke** |

---

## 5. Experiment protocol (production-grade)

### 5.1 Rules

1. **One hypothesis per run** — change only the variables listed for that experiment ID.
2. **Freeze denominator** — always report L1/L2/L3 using §2 identities.
3. **Paired metrics** — every IPC improvement must report:
   - `obligation_ipc_rate`, `compare_queued`, `post_validation_compared`
   - `wrong_policy_blocked`, `routing_validation_rejected`
   - `violations` (NC count) on 429-clean run
   - `skip_by_reason` delta vs previous artifact
4. **Artifact naming** — `outputs/atlassian_ipc3_E-<id>_<date>.json`
5. **Rollback** — revert env/code for failed experiment before starting next.

### 5.2 Commands (every experiment)

```powershell
cd d:\Ankit_legal\Legal\temp_java_sync

# Pre-flight
python atlassian_ipc2.py                                    # sync preflight if E-IDX
python -c "from _verify_pr01_settings import resolved_pr01_settings; import json; print(json.dumps(resolved_pr01_settings(), indent=2))"

# Smoke
python run_pr01_atlassian_smoke.py                          # exit 2 = gates fail OK

# Reports
python _ipc_reason_report.py outputs/atlassian_pr01_smoke.json
python -c "
import json; from pathlib import Path
d=json.loads(Path('outputs/atlassian_pr01_smoke.json').read_text())
f=d['compliance_stats']['obligation_pipeline_funnel']
pre=sum(v for k,v in f['skip_by_reason'].items() if k!='evidence_sufficient')
print('IDENTITY', f['extracted'], pre+f['compare_queued'], pre+f['llm_ipc_count']+f['post_validation_compared'])
print('FUNNEL', f)
"
```

### 5.3 Pass / fail template

```text
Experiment: E-___
Change: ___
Bucket schema: ipc3_bucket_v1 → ___   (bump if semantics changed — see §2.0)
Buckets redefined this run: [ ] none  [ ] boilerplate  [ ] routing_or_skip  [ ] evidence_*

Baseline → Result (layer totals — always comparable):
  PRE_IPC (L1):              37 → ___
  compare_queued (QUEUED):     29 → ___
  llm_ipc_count (L2):          14 → ___
  post_validation_compared:    15 → ___
  obligation_ipc_rate:      0.773 → ___

Bucket detail (comparable only if schema unchanged):
  routing_or_skip:            14 → ___
  boilerplate:                17 → ___
  wrong_policy_blocked:        0 → ___
  llm_rate_limit_events:       8 → ___

Paired precision (if applicable): ___
Decision: SHIP / HOLD / ROLLBACK
```

---

## 6. Implementation phases (strict order)

```text
Track A — Infrastructure (parallel, user-operated)
  E-INF1  429 / key pool alignment

Track B — Index (prerequisite for routing precision)
  E-IDX   OB-02B deploy + re-sync + IPC-2 gate

Track C — Pre-LLM funnel (engine, measured)
  E-BP    Boilerplate audit + targeted PR-06C
  E-RT2   Catalog threshold (only if routing_or_skip still > 10 after E-IDX)
  E-EV1   PR-04B semantic overlap
  E-EV2   Threshold loosen (last resort, paired precision)

Track D — Post-LLM precision
  E-LLM1  Obligation compare context + prompt

Track E — Validation
  E-VAL   Full battery vs golden NC fixtures
```

---

### Phase 0 — IPC-3 measurement (order matters)

```text
IPC0-R  →  IPC3-0E (3 smokes)  →  IPC3-0A–D (freeze + audit)
```

**Do not run IPC3-0A–D before IPC0-R.** Do not run E-IDX before IPC3-0E.

#### IPC3-0E — Variance band (**first measurable step after IPC0-R**)

| Task | Action |
|------|--------|
| IPC3-0E0 | Confirm **IPC0-R** complete (v1 prompt, flags off) |
| IPC3-0E1 | 3× `run_pr01_atlassian_smoke.py` → `outputs/ipc3_variance_run_{1,2,3}.json` |
| IPC3-0E2 | Write `outputs/ipc3_variance_summary.json` (min / max / median per layer) |
| IPC3-0E3 | Update §1.3 measured table in this doc |

**Pass:** 3 runs committed; band is authoritative. **BLOCKS E-IDX.**

#### IPC3-0A–D — Freeze + audit (after 0E)

**Operational lock:** IPC3-0A **must** copy `ipc3_variance_run_1.json` (not a fresh smoke). Baseline freeze and audit export use the **same R0 frozen config** as all three variance runs — do not re-run smoke for 0A unless variance run #1 is missing.

| Task | Action | Deliverable |
|------|--------|-------------|
| IPC3-0A | Copy variance run #1 → `atlassian_ipc3_baseline.json` | Frozen reference |
| IPC3-0B | `_ipc_reason_report.py` on baseline | Report snippet |
| IPC3-0C | Export → `ipc3_obligation_audit.jsonl` | E-BP1 input |
| IPC3-0D | Funnel identity assert; log `ipc3_bucket_v1` | Sum check only |

**Bucket schema:** `review/plans/ipc3_bucket_schema_v1.json`

---

### Experiment E-INF1 — 429 / key pool (0.5 day, operator)

**Hypothesis:** Section compare completes → ↓ section IPC; marginal L1 effect only.

| Step | Action |
|------|--------|
| E-INF1A | Upgrade Mistral tier / quota |
| E-INF1B | `review_agent/.env`: `LLM_KEY_POOL_ENABLED=true`, paste 3 real keys (match `temp_java_sync/.env`) |
| E-INF1C | Re-smoke; require `llm_rate_limit_events=0`, `section_compare_failed` findings = 0 |

**Pass:** `llm_rate_limit_events=0`; `section_ipc_pct` drops ≥ 10 pts vs baseline.  
**Fail action:** Do not tune obligation gates until E-INF1 passes — NC count is meaningless under 429.

---

### Experiment E-IDX — OB-02B index deploy + re-sync (0.5 day)

**Prerequisite:** OB-02B **code** on document-mcp build (not re-sync alone).

| Step | Action |
|------|--------|
| E-IDX1 | Restart `start_document_mcp.ps1 -Replace` (verify build includes tagger changes) |
| E-IDX2 | `document_core/.env`: `CATEGORY_TAGGER_MODE=llm` |
| E-IDX3 | Full Atlassian policy sync (`atlassian-demo` / `e2e-demo`) |
| E-IDX4 | `python atlassian_ipc2.py` → `validate_policy_sync()` returns `[]` |
| E-IDX5 | Spot-check ≥5 AUP sections in DB — specific tags, not uniform `compliance/security` |
| E-IDX6 | Re-smoke **no other env changes** |

**Expected delta (hypothesis only — not a pass/fail gate):**

| Metric | Hypothesis if index fix works |
|--------|------------------------------|
| `routing_or_skip` | 14 → **< 10** |
| `low_concept_overlap` | 2 → **≤ 2** |
| `llm_ipc_count` | 14 → **< 12** |
| `weak_tag_count` | 1 → **0** |

**Pass (hard gates — operator must satisfy all):**

1. `validate_policy_sync()` returns `[]`
2. `weak_tag_count=0`, all policies `tagger=llm`
3. E-IDX6 smoke: `llm_rate_limit_events=0` (or document E-INF1 still pending)
4. Layer improvement: `PRE_IPC` ↓ **or** `QUEUED` ↑ vs **variance band median** (same bucket schema)

**Decision if hypothesis missed but hard gates pass:**

| Outcome | Example | Action |
|---------|---------|--------|
| Index fixed, routing improved modestly | `routing_or_skip` 14 → 11 | **SHIP E-IDX** — index goal met |
| E-RT2 trigger | `routing_or_skip` still **> 10** | Proceed to E-RT2 per §6 (do not re-run E-IDX) |
| No layer movement | `PRE_IPC` unchanged, `weak_tag_count=0` | **HOLD** — investigate AUP spot-check failures before E-RT2 |

**Do not fail E-IDX** solely because `routing_or_skip=11` instead of `<10`. The `<10` target is for **E-VAL** final battery; E-IDX pass is IPC-2 + layer movement vs **variance band**.

**If E-IDX hard gates fail (distinct from rollback):**

| Failure | Meaning | Action — **do not** re-sync again blindly |
|---------|---------|------------------------------------------|
| `validate_policy_sync()` errors | Sync harness / MCP / DB | Fix errors in log; check MCP health; re-run E-IDX3–4 only |
| `weak_tag_count > 0` after re-sync | OB-02B tagger code not on MCP or prompt not applied | `git log` MCP build; confirm `document_core` changes in running process; fix tagger, restart MCP, re-sync |
| AUP spot-check fails (§E-IDX5) | Tags still broad | **Stop E-IDX** — debug OB-02B (`category_tagger.py`, prompt, priors) before third re-sync |
| E-IDX6 smoke: no layer movement vs band, hard gates pass | Index OK; routing variance or wrong policy index tenant | Compare `routing_or_skip` to band; inspect catalog hits; consider E-RT2 — **not** another full re-sync |

**Rollback (regression — made things worse):** Re-sync from `sync_atlassian_e2e-demo.json` snapshot. Use only when sync **corrupted** index or wrong tenant — not when tagger logic is broken.

**Rollback vs debug:** Rollback = restore data; Debug = fix code when hard gates fail.

---

### Experiment E-BP — Boilerplate precision (1 day)

**Hypothesis:** False boilerplate skips are material; rule override recovers compare queue without spam.

**Code status:** E-BP2 below is a **draft spec only** — do not implement until E-BP1 audit completes and tier rule selects E-BP2.

#### E-BP1 — Manual audit (required before any code)

| Step | Action |
|------|--------|
| E-BP1A | Export 17 obligations → `outputs/ipc3_boilerplate_audit.jsonl` (IPC3-0C) |
| E-BP1B | Rater A labels all 17 using rubric below |
| E-BP1C | Rater B (second person or blind re-read after 24h) labels **5 stratified samples**: 2 shortest quotes, 2 longest, 1 with `explicit_policy_mentions` if any |
| E-BP1D | **Agreement:** ≥4/5 match between raters; if <4/5, discuss disagreements and re-label all 17 before proceeding |
| E-BP1E | Compute `false_skip_rate = FALSE_SKIP / 17` |

**FALSE_SKIP rubric (must cite one primary criterion in audit JSONL):**

| Label | Criteria — mark **FALSE_SKIP** if **any** apply |
|-------|--------------------------------------------------|
| **CORRECT_SKIP** | Pure definition / cross-ref / notice mechanics / signature / governing-law boilerplate with **no** testable compliance duty |
| **FALSE_SKIP** | Imposes or references a **testable duty** (payment, liability, data, security, termination, IP, SLA, indemnity, audit rights) |
| **FALSE_SKIP** | Names a **specific policy document** (DPA, AUP, Product Terms) or contains `explicit_policy_mentions` |
| **FALSE_SKIP** | Would be **material to NC** if wrong (operator judgment — document reason in audit row) |

**Audit row schema:**

```json
{"obligation_id": "...", "section_id": "...", "quote_snip": "...", "rater": "A", "label": "CORRECT_SKIP", "primary_criterion": "pure_definition", "notes": ""}
```

**Decision rule:**

| `false_skip_rate` | Action |
|-------------------|--------|
| **≤ 2/17 (< 12%)** | **No E-BP2.** Boilerplate gate OK; proceed E-IDX / E-EV1 |
| **3–6/17 (12–35%)** | Implement E-BP2 only; re-smoke; bump bucket schema to `v2` |
| **≥ 7/17 (> 35%)** | E-BP2 + E-BP3 planner/extract prompt review |

**Boundary:** At exactly 2/17 (11.8%) → **No E-BP2** (round down, conservative on code change).

#### E-BP2 — Code: substantive override (PR-06C extension) — **gated by E-BP1**

**File:** `obligation_retrieval.py` + `catalog_matcher.py`

```python
# When plan.routing_source == "skipped_boilerplate", do NOT skip if:
override = (
    ob.explicit_policy_mentions
    or plan.confidence >= settings.routing_planner_explicit_mention_confidence_floor  # 0.55
    or ob.obligation_type not in ("boilerplate", "general", "")
)
if plan.routing_source == "skipped_boilerplate" and not override:
    return skipped_reason="boilerplate"
# else fall through to normal catalog + retrieval
```

**Files to touch:**

| File | Change |
|------|--------|
| `obligation_retrieval.py` | Gate boilerplate skip on `override` |
| `catalog_matcher.py` | Same override before `route_decision=ipc` for boilerplate |
| `tests/test_obligation_retrieval.py` | Named-policy mention in boilerplate section → retrieval runs |
| `tests/test_catalog_matcher.py` | Boilerplate + explicit mention → not instant IPC |

**Pass (isolated smoke):** Bump bucket schema `ipc3_bucket_v2`.

- `PRE_IPC` ↓ ≥ 3 **and** `QUEUED` ↑ ≥ 3 (layer totals — §2.0)
- `wrong_policy_blocked` stays 0
- No new NC on sections 15, 19, 20.4 (spot check)
- Re-audit: ≤1/10 newly queued obligations labeled `FALSE_POSITIVE` (paired precision)

**Anti-pattern:** Do not set `infer_obligation_boilerplate` to always `False`.

---

### Experiment E-RT2 — Catalog threshold (0.5 day, conditional)

**Gate to start:** Only after E-IDX passes **and** `routing_or_skip` still **> 10**.

**Hypothesis:** Marginal catalog hits inside tenant fence recover routing without wrong-policy matches.

| Variable | Baseline | E-RT2 value |
|----------|----------|-------------|
| `CATALOG_MATCH_MIN_SCORE` | 0.25 | **0.22** |
| `catalog_match_marginal_floor` | 0.85 × min | keep code default |

**Do NOT combine with** `CATALOG_MATCH_MAX_CANDIDATES` change in same run.

**Pass:**

- `routing_or_skip` ↓ ≥ 4
- `wrong_policy_blocked` = 0
- `obligation_ipc_rate` ↓ ≥ 0.05

**Fail:** Revert `CATALOG_MATCH_MIN_SCORE`; do not proceed to E-EV2.

---

### Experiment E-EV1 — PR-04B semantic evidence overlap (1.5 days) ⭐ precision-safe

**Goal:** Replace lexical-only veto for paraphrase pairs without lowering lexical thresholds.

#### E-EV1-0 — Threshold calibration (required before shipping config)

**Do not set `EVIDENCE_MIN_SEMANTIC_OVERLAP=0.72` until calibration artifact exists.**

| Step | Action |
|------|--------|
| E-EV1-0A | From baseline smoke, sample **20 obligation×hit pairs**: 10 known `low_concept_overlap` IPC + 10 known `evidence_sufficient` compare |
| E-EV1-0B | Add **10 wrong-fence pairs**: obligation vs hit from non-candidate doc (should stay blocked) |
| E-EV1-0C | Run `scripts/calibrate_semantic_overlap.py` (or notebook) — embed both sides, record cosine sim |
| E-EV1-0D | Plot / table: TP paraphrase sims vs FP wrong-policy sims |
| E-EV1-0E | Choose threshold at **maximize TP−FP**: target ≥90% TP pass, ≥90% FP block on calibration set |
| E-EV1-0F | Commit `outputs/ipc3_semantic_overlap_calibration.json` with chosen threshold |

**Starting search grid:** 0.65, 0.68, 0.72, 0.75 — pick from calibration, not default.

**If calibration spread overlaps** (TP and FP same band): ship E-EV1 mechanism with `EVIDENCE_SEMANTIC_OVERLAP_ENABLED=false` in prod; escalate to embedding model review — do not guess 0.72.

#### E-EV1A — New module

**File:** `review_agent/services/concept_overlap.py`

```python
def semantic_concept_overlap(
    obligation_text: str,
    plan_concepts: list[str],
    hits: list[RetrievalHit],
    *,
    settings: ReviewSettings,
) -> float:
    """Max cosine similarity between obligation embedding and hit parent text (cached)."""
```

- Reuse `document_core` embedder (same model as hybrid search).
- Cache key: `obligation_id` + hit `chunk_id` within review scope.

#### E-EV1B — Integrate in `evidence_sufficiency.py`

```python
def _hits_pass_gates(...):
    if existing_lexical_or_rerank_pass:
        return True
    if cfg.evidence_semantic_overlap_enabled:
        sem = semantic_concept_overlap(...)
        if sem >= cfg.evidence_min_semantic_overlap:
            return True
    return False
```

**New config (values from E-EV1-0 calibration artifact):**

```env
EVIDENCE_SEMANTIC_OVERLAP_ENABLED=true
EVIDENCE_MIN_SEMANTIC_OVERLAP=<from ipc3_semantic_overlap_calibration.json>
```

#### E-EV1C — Tests

| Test | Case |
|------|------|
| `test_evidence_sufficiency_semantic_paraphrase` | High rerank, zero token overlap, semantic sim 0.75 → `compare` |
| `test_evidence_sufficiency_semantic_block` | Low semantic sim 0.4 → stays `ipc` |
| `test_evidence_sufficiency_wrong_policy` | High semantic to wrong-doc hit outside fence → still blocked by catalog |

**Pass:**

- `low_concept_overlap` + `low_relevance_score` + `insufficient_evidence` combined **≤ 4** (from 6)
- `compare_queued` **≥ 33**
- `wrong_policy_blocked` = 0

---

### Experiment E-EV2 — Threshold loosen (0.25 day, last resort)

**Gate to start:** E-EV1 insufficient **and** E-IDX + E-BP shipped.

| Variable | Step |
|----------|------|
| `EVIDENCE_MIN_CONCEPT_OVERLAP` | 0.15 → **0.12** (one step only) |
| `EVIDENCE_MIN_SCORE` | keep 0.35 unless paired review |

**Required paired check:**

- Sample 10 newly queued obligations (were IPC at L1 in baseline)
- Manual label: `VALID_COMPARE` \| `FALSE_POSITIVE`
- **Ship only if** `FALSE_POSITIVE` ≤ 2/10

**Fail:** Revert overlap to 0.15 immediately.

---

### Experiment E-LLM1 — Post-LLM IPC reduction (1 day)

**Gate:** `compare_queued` ≥ 33 from prior experiments. **E-IDX complete.** **IPC0-R already split v1/v2 files.**

**Prerequisite:** `obligation_compare_v1.md` and `obligation_compare_v2.md` exist; active file selected by flag only.

```env
OBLIGATION_COMPARE_PROMPT_V2_ENABLED=false   # default until E-LLM1 smoke
```

| Task | Detail |
|------|--------|
| E-LLM1A | v2 prompt in `obligation_compare_v2.md` only (never overwrite v1) |
| E-LLM1B | Pass top **4** hits per obligation (today may truncate to fewer in batch formatter) — `obligation_compare_llm.py` `_format_obligations_block` |
| E-LLM1C | Include parent section title + `policy_ref` breadcrumb in each hit block |

**Pass:**

- `llm_ipc_count` 14 → **< 8**
- `post_validation_compared` **≥ 22**
- Grounded quote rate on compared findings not ↓

---

### Experiment E-VAL — Golden validation (0.5 day)

**Gate:** 429-clean run (`llm_rate_limit_events=0`).

| Gate | Threshold |
|------|-----------|
| `obligation_ipc_rate` | **< 0.50** |
| `post_validation_compared` | **≥ 20** |
| `compare_queued` | **≥ 35** |
| `routing_or_skip` | **< 10** |
| `wrong_policy_blocked` | **0** |
| Atlassian NC | **≥ 4** |
| Cisco / Xecurify regression | No NC regression vs last green battery |

```bash
cd Legal/temp_java_sync
python run_battery_collect.py
python _ipc_reason_report.py outputs/atlassian_pr01_smoke.json
```

---

## 7. Production design patterns

| Pattern | Implementation in IPC-3 |
|---------|-------------------------|
| **Scoped fence** | All catalog/retrieval relaxations stay inside `allowed_doc_ids` |
| **LLM-as-judge** | L1 blocks noise only; L3 owns compliance verdict |
| **Index before gates** | E-IDX before E-RT2 / E-EV2 |
| **Semantic over lexical** | E-EV1 before threshold knobs |
| **Audit before prompt** | E-BP1 before boilerplate code |
| **One change per run** | §5 experiment protocol |
| **INCONCLUSIVE ≠ COMPLIANT** | Keep IPC for empty fence / incompatible family |

### Anti-patterns (explicitly forbidden)

- Re-sync policies without OB-02B code on MCP → false negative on E-IDX
- Lower `EVIDENCE_MIN_CONCEPT_OVERLAP` to 0 “to fix IPC”
- Raise `CATALOG_MATCH_MAX_CANDIDATES` and lower `min_score` in same run
- Judge NC success while `llm_rate_limit_events` > 0
- Disable boilerplate skips globally
- **Merge IPC-3 code + E-LLM1 prompt + smoke once** without E-IDX (caused 0.773→0.818 false alarm)
- **Declare regression from one smoke** outside variance band (§1.3)

---

**Pass:** Bump bucket schema `ipc3_bucket_v3`.

- `PRE_IPC` ↓ or `QUEUED` ↑ vs prior run (same schema chain)
- RC-EV bucket sum (`low_*` + `insufficient_evidence`) **≤ 4** (from 6)
- `compare_queued` **≥ 33**
- `wrong_policy_blocked` = 0
- Calibration holdout: ≥8/10 TP pass, ≥8/10 FP block on E-EV1-0 pairs

---

## 8. Expected cumulative impact (honest ranges — not commitments)

Per-experiment deltas are **directional hypotheses**. Stacked range is a **sanity check only**.

### 8.1 Per-experiment (isolated)

| Experiment | Primary layer | `obligation_ipc_rate` delta | Confidence |
|------------|---------------|----------------------------|------------|
| E-INF1 alone | Section / L2 edge | −0.03 to −0.05 | High section; low obligation |
| E-IDX alone | L1 + L2 | −0.08 to −0.12 | Medium |
| E-BP (if audit warrants) | L1 | −0.05 to −0.08 | Medium — audit-dependent |
| E-RT2 | L1 | −0.03 to −0.06 | Medium |
| E-EV1 | L1 | −0.04 to −0.07 | Medium until calibration done |
| E-LLM1 | L2 only | −0.06 to −0.10 | Medium |

### 8.2 Stacked estimate method (do not linearly sum)

Use **layer budget** from baseline:

```text
L1 PRE_IPC = 37  → target ≤ 20  (need −17)
L2 llm_ipc   = 14  → target ≤ 8   (need −6)
L3 compared  = 15  → target ≥ 25  (need +10)

ipc_rate = (L1 + L2) / 66
Target: (20 + 8) / 66 ≈ 0.42
```

**Overlap accounting:**

- E-IDX helps L1 (`routing_or_skip`) **and** L2 (better hits → fewer LLM IPC) — count once toward L1, tag L2 as secondary
- E-LLM1 helps L2 only — no L1 credit
- E-INF1 helps section path — subtract from section IPC, not L1 budget

**Stacked sanity range 0.42–0.48** = if L1 reaches 20–22 and L2 reaches 8–10 simultaneously. **Not a sprint commitment** — E-VAL gates (`ipc_rate < 0.50`) are the actual ship bar.

**Do not sum upper bounds linearly** — experiments overlap on shared retrieval quality.

---

## 9. File change matrix

| Experiment | Files | Tests |
|------------|-------|-------|
| E-IDX | *(operator)* document-mcp restart, sync | `atlassian_ipc2.py`, manual AUP spot-check |
| E-BP2 | `obligation_retrieval.py`, `catalog_matcher.py` | `test_obligation_retrieval.py`, `test_catalog_matcher.py` |
| E-EV1 | `concept_overlap.py`, `evidence_sufficiency.py`, `config.py` | `test_evidence_sufficiency.py` |
| E-LLM1 | `obligation_compare.md`, `obligation_compare_llm.py` | `test_obligation_compare_llm.py` |
| IPC3-0D | `run_pr01_atlassian_smoke.py` | `test_review_preflight.py` or smoke self-check |

---

## 10. Rollback matrix

| Experiment | Rollback |
|------------|----------|
| E-IDX | Re-sync from `sync_atlassian_e2e-demo.json` backup |
| E-BP2 | Revert code commit |
| E-RT2 | `CATALOG_MATCH_MIN_SCORE=0.25` |
| E-EV1 | `EVIDENCE_SEMANTIC_OVERLAP_ENABLED=false` |
| E-EV2 | Restore overlap 0.15 |
| E-INF1 | Keys/tier — no code rollback |

---

## 11. Checklist (operator) — strict order

- [ ] **IPC0-R:** v1 prompt restored; v1+v2 files; `OBLIGATION_COMPARE_PROMPT_V2_ENABLED=false`
- [ ] **IPC-NC1:** Empty-quote NC `9:6` filed + `quote_validate` behavior checked
- [ ] **IPC3-0E:** 3-run variance band → `ipc3_variance_summary.json` (**blocks E-IDX**)
- [ ] IPC3-0A–D: Baseline frozen + audit JSONL + bucket schema v1
- [ ] E-INF1: `llm_rate_limit_events=0` on band runs (or documented exception)
- [ ] **E-IDX:** Only after band — hard-gate fail → debug table §6, not blind re-sync
- [ ] E-BP1: Audit rubric + rater agreement
- [ ] E-BP2 / E-EV1 / E-RT2 / E-LLM1: one flag per smoke vs band
- [ ] E-VAL: `obligation_ipc_rate` < 0.50; NC ≥ 4 on 429-clean run

---

## 12. Related plans

| Plan | Relationship |
|------|--------------|
| [PHASE_PR01](./PHASE_PR01_PRECISION_FUNNEL_RECOVERY_PLAN.md) | Code foundation — **IMPLEMENTED**; IPC-3 is execution + measurement layer |
| [PHASE_OB02B](./PHASE_OB02B_PR02B_POLICY_TAGGER_QUALITY_PLAN.md) | E-IDX prerequisite — code done, operator re-sync pending |
| [PHASE_MCP_GLOBAL_CONCURRENCY](./PHASE_MCP_GLOBAL_CONCURRENCY_PLAN.md) | DONE — keep `breaker_open_events_mcp=0` on every run |

**IPC-3 supersedes** informal IPC summaries in chat: use §2 identities and §5 experiment protocol as the source of truth for prioritization and expected impact.
