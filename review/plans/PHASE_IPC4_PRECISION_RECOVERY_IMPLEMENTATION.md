# Phase IPC-4 — Precision Recovery Implementation (Execute Now)

**Version:** 1.0  
**ID:** `DR-PHASE-IPC4`  
**Parent:** [PHASE_IPC3_PRODUCTION_EXECUTION_PLAN.md](./PHASE_IPC3_PRODUCTION_EXECUTION_PLAN.md) · [PHASE_OB02B_PR02B_POLICY_TAGGER_QUALITY_PLAN.md](./PHASE_OB02B_PR02B_POLICY_TAGGER_QUALITY_PLAN.md)  
**Baseline band:** `temp_java_sync/outputs/ipc3_variance_summary.json` (median IPC 0.887, `routing_or_skip` 19, `QUEUED` 27)  
**Status:** **IN PROGRESS** — Batch A+B+C code shipped 2026-06-25

---

## 1. Problem → fix map (precise)

| Bucket | Median | Root cause | Fix (this phase) |
|--------|--------|------------|------------------|
| `routing_or_skip` | 19 | Stale index + weak AUP tags | **E-IDX** re-sync with OB-02B tagger |
| `low_concept_overlap` | 4–6 | Lexical Jaccard veto | **E-EV1** semantic gate (flag off default) |
| `boilerplate` | 13–17 | Deterministic skip | **E-BP2** (flag off; audit before enable) |
| `low_routing_confidence` | varies | Planner confidence < 0.6 | Addressed after E-IDX; optional E-RT2 |
| LLM IPC | 8–11 | Thin context / prompt | **E-LLM1** after L1 improves |
| 429 | 6 | Mistral quota | **E-INF1** operator |

**Do not loosen all gates at once.** One experiment per smoke vs band median.

---

## 2. Implementation batches (code in this PR)

### Batch A — OB-02B ingest fix (E-IDX prerequisite)

| File | Change |
|------|--------|
| `document_core/services/metadata_at_ingest.py` | Infer from **title** when `section_texts` empty |
| `tests/test_metadata_at_ingest.py` | Pass liability title test |

### Batch B — E-EV1 semantic evidence gate (default OFF)

| File | Change |
|------|--------|
| `review_agent/services/concept_overlap.py` | `semantic_concept_overlap()` via `document_core.embeddings` |
| `review_agent/services/evidence_sufficiency.py` | Pass if semantic ≥ threshold OR existing gates |
| `tests/test_evidence_sufficiency.py` | Paraphrase case with mocked embeddings |
| `config.py` + `.env` | `EVIDENCE_SEMANTIC_OVERLAP_ENABLED=false` |

### Batch C — Operator tooling

| File | Purpose |
|------|---------|
| `temp_java_sync/run_e_idx_atlassian.py` | Sync 9 policies + IPC-2 validate + `weak_tag_count` |
| `temp_java_sync/export_ipc3_audit.py` | Export obligation IPC rows for E-BP1 audit |

### Batch D — Already shipped (IPC-3)

- IPC0-R prompt loader, IPC3 gates, funnel check, NC quote_validate fix, E-BP2 behind flag

---

## 3. Execution order (operator)

```text
1. pytest document_core + review_agent IPC tests          ← verify Batch A+B
2. python temp_java_sync/run_e_idx_atlassian.py         ← E-IDX
3. python run_pr01_atlassian_smoke.py                   ← one smoke
4. python ipc3_funnel_check.py outputs/atlassian_pr01_smoke.json
5. Compare vs band median (not 0.773):
     routing_or_skip < 19  OR  compare_queued > 27  OR  PRE_IPC < 44
6. If routing_or_skip still > 14: E-RT2 (catalog min_score 0.22) — separate smoke
7. E-BP1 audit → maybe IPC3_BOILERPLATE_SUBSTANTIVE_OVERRIDE_ENABLED=true
8. E-EV1-0 calibration → EVIDENCE_SEMANTIC_OVERLAP_ENABLED=true
```

---

## 4. E-IDX pass / fail

**Pass (hard):**
- `validate_policy_sync()` → `[]`
- `weak_tag_count=0`

**Pass (smoke vs band):**
- `PRE_IPC` < band median **OR** `compare_queued` > band median

**Fail — debug (not blind re-sync):**
- `weak_tag_count>0` → fix tagger on MCP, restart, re-sync
- AUP spot-check fails → OB-02B code path
- No layer movement → E-RT2 only (not another full re-sync)

---

## 5. Target after E-IDX + E-EV1 (honest)

| Metric | Band median now | Target post E-IDX+E-EV1 |
|--------|-----------------|-------------------------|
| `obligation_ipc_rate` | 0.887 | **< 0.65** (step); **< 0.50** (E-VAL) |
| `compare_queued` | 27 | **≥ 32** |
| `routing_or_skip` | 19 | **< 14** |
| Compared | 8 | **≥ 15** |

E-VAL `< 0.50` requires stacked experiments, not E-IDX alone.
