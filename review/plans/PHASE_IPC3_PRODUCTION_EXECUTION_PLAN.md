# Phase IPC-3 — Production IPC Recovery (Execution Plan)

**Version:** 1.1  
**ID:** `DR-PHASE-IPC3`  
**Parent:** [PHASE_PR01_PRECISION_FUNNEL_RECOVERY_PLAN.md](./PHASE_PR01_PRECISION_FUNNEL_RECOVERY_PLAN.md) · [PHASE_OB02B_PR02B_POLICY_TAGGER_QUALITY_PLAN.md](./PHASE_OB02B_PR02B_POLICY_TAGGER_QUALITY_PLAN.md) · [PHASE_MCP_GLOBAL_CONCURRENCY_PLAN.md](./PHASE_MCP_GLOBAL_CONCURRENCY_PLAN.md)  
**Baseline artifact:** `temp_java_sync/outputs/atlassian_pr01_smoke.json` (2026-06-30, `parallel_hybrid`, post PR-01 code + MCP semaphore)  
**Status:** **PLANNED** — execute in strict experiment order below  
**Out of scope:** Graph rewrite, disabling `policy_coverage`, global boilerplate off, blind threshold lowering without precision gates  

---

## 1. Executive summary

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

### Phase 0 — IPC-3 measurement baseline (0.25 day)

**Goal:** Freeze reconciled baseline; export audit inputs.

| Task | Action | Deliverable |
|------|--------|-------------|
| IPC3-0A | Copy smoke → `outputs/atlassian_ipc3_baseline.json` | Frozen reference |
| IPC3-0B | Run `_ipc_reason_report.py` on baseline | Committed report snippet in PR |
| IPC3-0C | Export 17 boilerplate + 14 routing_or_skip obligation texts | `outputs/ipc3_boilerplate_audit.jsonl` |
| IPC3-0D | Funnel identity assert in smoke stderr; **log `skip_by_reason` schema version** `ipc3_bucket_v1` | Fail fast if sums break; freeze bucket semantics at baseline |

**Bucket schema version:** Commit `outputs/ipc3_bucket_schema_v1.json` listing each `skip_by_reason` key → code path that sets it (file + function). Bump version when E-BP2 / E-EV1 / E-RT2 ship; post-bump runs compare layer totals only vs prior run with same schema version.

**Script (IPC3-0C):**

```python
# outputs/export_ipc_audit.py — extract obligation IPC rationales by skip reason
import json
from pathlib import Path
review = json.loads(Path("atlassian_pr01_smoke.json").read_text(encoding="utf-8"))
for f in review["findings"]:
    m = f.get("metadata") or {}
    if m.get("source") != "obligation_ipc":
        continue
    r = f.get("rationale", "")
    for tag in ("boilerplate", "routing_or_skip", "low_relevance_score", "low_concept_overlap"):
        if f"({tag})" in r or f": {tag}" in r:
            print(json.dumps({"id": f.get("dimension_id"), "section": f.get("contract_section_id"), "tag": tag, "quote": (f.get("contract_quote") or "")[:300]}))
            break
```

**Pass:** Baseline identities in §2 all hold; audit file has 31 rows (17+14).

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
4. Layer improvement: `PRE_IPC` ↓ **or** `QUEUED` ↑ vs previous artifact (same bucket schema)

**Decision if hypothesis missed but hard gates pass:**

| Outcome | Example | Action |
|---------|---------|--------|
| Index fixed, routing improved modestly | `routing_or_skip` 14 → 11 | **SHIP E-IDX** — index goal met |
| E-RT2 trigger | `routing_or_skip` still **> 10** | Proceed to E-RT2 per §6 (do not re-run E-IDX) |
| No layer movement | `PRE_IPC` unchanged, `weak_tag_count=0` | **HOLD** — investigate AUP spot-check failures before E-RT2 |

**Do not fail E-IDX** solely because `routing_or_skip=11` instead of `<10`. The `<10` target is for **E-VAL** final battery; E-IDX pass is IPC-2 + layer movement.

**Rollback:** Re-sync from last good `sync_atlassian_e2e-demo.json` snapshot.

---

### Experiment E-BP — Boilerplate precision (1 day)

**Hypothesis:** False boilerplate skips are material; rule override recovers compare queue without spam.

#### E-BP1 — Manual audit (required before code)

| Step | Action |
|------|--------|
| E-BP1A | Label each of 17 obligations: `CORRECT_SKIP` \| `FALSE_SKIP` |
| E-BP1B | Compute `false_skip_rate = FALSE_SKIP / 17` |

**Decision rule:**

| `false_skip_rate` | Action |
|-------------------|--------|
| **< 15%** (≤2) | **No prompt change.** Boilerplate working; focus E-IDX / E-EV1 |
| **15–35%** (3–6) | Ship E-BP2 targeted override only |
| **> 35%** | E-BP2 + E-BP3 planner/extract prompt review |

#### E-BP2 — Code: substantive override (PR-06C extension)

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

**Pass (isolated smoke):**

- `boilerplate` skip ↓ by ≥ 3 **and** `compare_queued` ↑ by ≥ 3
- `wrong_policy_blocked` stays 0
- No new NC on sections 15, 19, 20.4 (spot check)

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

**Goal:** Replace lexical-only veto for paraphrase pairs without lowering thresholds.

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

**New config:**

```env
EVIDENCE_SEMANTIC_OVERLAP_ENABLED=true
EVIDENCE_MIN_SEMANTIC_OVERLAP=0.72
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

**Gate:** `compare_queued` ≥ 33 from prior experiments.

| Task | Detail |
|------|--------|
| E-LLM1A | `obligation_compare.md`: distinguish “policy silent on topic” (INCONCLUSIVE) vs “policy clearly addresses topic” (must pick COMPLIANT/NC) |
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

---

## 8. Expected cumulative impact (honest ranges)

| Experiment | `obligation_ipc_rate` delta | Confidence |
|------------|----------------------------|------------|
| E-INF1 alone | −0.03 to −0.05 (section path) | High for section; low for obligation |
| E-IDX alone | −0.08 to −0.12 | Medium — depends on AUP tag distribution |
| E-BP (if false_skip ≥ 15%) | −0.05 to −0.08 | Medium — audit-dependent |
| E-EV1 | −0.04 to −0.07 | High if semantic embed quality matches hybrid |
| E-LLM1 | −0.06 to −0.10 on L2 only | Medium |
| **Stacked (all pass)** | **0.77 → 0.42–0.48** | Target range |

**Do not sum upper bounds linearly** — experiments overlap (better tags help both L1 and L2).

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

## 11. Checklist (operator)

- [ ] IPC3-0: Baseline frozen + funnel identities verified
- [ ] E-INF1: 3 real keys both `.env`; `llm_rate_limit_events=0` on smoke
- [ ] E-IDX: MCP restarted; `weak_tag_count=0`; AUP spot-check
- [ ] E-BP1: Boilerplate audit JSONL completed; decision rule applied
- [ ] E-BP2: Code shipped only if audit warrants
- [ ] E-RT2: Only if `routing_or_skip` > 10 post E-IDX
- [ ] E-EV1: Semantic overlap shipped + tests green
- [ ] E-EV2: Only if E-EV1 insufficient + precision sample pass
- [ ] E-LLM1: `llm_ipc_count` < 8
- [ ] E-VAL: `obligation_ipc_rate` < 0.50; NC ≥ 4; battery clean

---

## 12. Related plans

| Plan | Relationship |
|------|--------------|
| [PHASE_PR01](./PHASE_PR01_PRECISION_FUNNEL_RECOVERY_PLAN.md) | Code foundation — **IMPLEMENTED**; IPC-3 is execution + measurement layer |
| [PHASE_OB02B](./PHASE_OB02B_PR02B_POLICY_TAGGER_QUALITY_PLAN.md) | E-IDX prerequisite — code done, operator re-sync pending |
| [PHASE_MCP_GLOBAL_CONCURRENCY](./PHASE_MCP_GLOBAL_CONCURRENCY_PLAN.md) | DONE — keep `breaker_open_events_mcp=0` on every run |

**IPC-3 supersedes** informal IPC summaries in chat: use §2 identities and §5 experiment protocol as the source of truth for prioritization and expected impact.
