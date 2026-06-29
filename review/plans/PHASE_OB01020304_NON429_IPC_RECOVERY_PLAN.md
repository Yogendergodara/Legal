# Phase OB-01/02/03/04 — Non-429 IPC Recovery (Obligation Funnel + Retrieval + Validation)

**Version:** 1.0  
**ID:** `DR-PHASE-OBIPC`  
**Parent:** [PHASE_SR01_MEANING_FIRST_RETRIEVAL_PLAN.md](./PHASE_SR01_MEANING_FIRST_RETRIEVAL_PLAN.md) · [PHASE_RC141516_QUOTE_IPC_CLARIFICATION_PLAN.md](./PHASE_RC141516_QUOTE_IPC_CLARIFICATION_PLAN.md) · [PHASE_RC080910_ROUTING_COMPARE_EXTRACT_PLAN.md](./PHASE_RC080910_ROUTING_COMPARE_EXTRACT_PLAN.md)  
**Targets:** Non-429 IPC on Atlassian hybrid battery — `section_path_resolved`, `coverage_gate_ipc`, `routing_validation_rejected`, `low_concept_overlap`  
**Status:** **IMPLEMENTED** (OB-01/03/04 code + env; OB-02A SR-01; OB-02B re-sync manual)  
**Scope:** Restore obligation compare funnel + section retrieval IPC + validation pass-through — **minimal code**, no graph rewrite, no batch-size reduction, scoped policies unchanged  
**Effort:** ~1.5 engineering days (OB-01 + OB-03 + OB-04 code/tests) + ~0.5 day (OB-02 IPC-2 re-sync + validation)  
**Risk:** Low–medium (OB-01 increases obligation MCP/LLM work — bounded by existing caps)

**Out of scope:** HTTP 429 / `section_compare_failed` — Phase B / quota (fix separately).

---

## 1. Problem statement

Atlassian LIVE (obligation ON, `parallel_hybrid`) — IPC breakdown **excluding 429**:

| Rank | IPC bucket | ~Count | Symptom |
|------|------------|--------|---------|
| **OB-01** | `obligation_ipc` / `section_path_resolved` | **~64** | Obligations never reach compare |
| **OB-02** | `coverage_gate_ipc` / tag overlap | **~11** | Section hits exist; compare skipped |
| **OB-03** | `routing_validation_rejected` | **1** (100% of queued) | Compare LLM output discarded |
| **OB-04** | `low_concept_overlap` (evidence skip) | **~10** | Obligation retrieval OK; evidence gate IPC |

**Pipeline stats (evidence):**

```text
obligation_retrieval_section_skip_count: 76
compare_queued: 1
obligation_ipc_findings: 108
routing_validation_rejected: 1
obligation_compare_count: 0
skip_by_reason: { section_path_resolved: 76, boilerplate: 21, low_concept_overlap: 10, evidence_sufficient: 1 }
```

**Gates passed:** 0/4 NC on latest hybrid run. Previous hybrid (less 429 damage) had 4 NC Atlassian.

---

## 2. Causal chain (ordered fix path)

```text
429 (Phase B — parallel)
        │
        ▼
OB-01  section_path_resolved falsely skips 76 obligations
        │  (parallel: skip runs BEFORE section compare)
        ▼
OB-02  coverage_gate blocks ~11 sections (tags / SR-01)
        │
        ▼
OB-03  validation fence rejects the 1 obligation that reached compare
        │
        ▼
OB-04  evidence gates block ~10 remaining obligation paths
```

**Production principle:** Fix **funnel blockers** (OB-01, OB-03) before **precision tuning** (OB-04). OB-02 is **SR-01 (shipped) + IPC-2 re-sync**.

---

## 3. Code-proven root causes

### OB-01 — `section_path_resolved` skip trusts retrieval, not compare outcome ⭐ P0

| Item | Detail |
|------|--------|
| **Where** | `review_graph.py` `_wire_parallel_hybrid_post_index` L139–148 · `obligation_retrieval_nodes.py` L88–101 · `obligation_retrieval.py` `should_skip_obligation_for_resolved_section` L170–196 |
| **Graph order** | `section_policy_retrieval` → `obligation_retrieval` → `evidence_sufficiency` → `pre_compare_join` → **`section_compare_llm` ∥ `obligation_compare`** |
| **Bug** | Skip runs when `section_bundle.policy_hits` + score ≥ `evidence_min_score`. Section compare **has not run** yet. Hits ≠ resolved compliance. |
| **Why 76 skips** | Retrieval found chunks; later section compare → IPC (429 or coverage). Obligation path already skipped. |
| **Existing guard** | `meta.get("coverage_gate_ipc")` — rarely set on bundle at skip time → ineffective |

**Misread:** “Turn off obligation routing.” → Routing is fine; **skip-resolved is wrong in parallel topology**.

---

### OB-02 — `coverage_gate_ipc` / weak tags ⭐ P1

| Item | Detail |
|------|--------|
| **Where** | `policy_coverage.py` `validate_section_coverage` · `retrieval_relevance.py` `has_specific_category_overlap` · sync preflight `weak_tag_count` |
| **Bug** | Semantic/reranked hit rejected when chunk/doc tags don’t overlap section categories |
| **SR-01** | **IMPLEMENTED** — meaning-first query, `category_hard_filter=false`, primary fallback |
| **Remaining** | **IPC-2** — policy index tags still weak on some Atlassian policies (`atlassian_ipc2.py`) |

---

### OB-03 — Routing validation rejects tenant-valid policy ⭐ P0 (when compare_queued > 0)

| Item | Detail |
|------|--------|
| **Where** | `routing_validation.py` `validate_obligation_compare_items` L67–79 |
| **Rule** | `policy_id not in candidate_doc_ids` → IPC (`no_invented_policies`) |
| **Bug** | LLM cites document in **tenant scoped set** (`allowed_doc_ids`) but outside **catalog matcher fence** (`candidate_doc_ids`) |
| **Effect** | `compare_queued=1` → `routing_validation_rejected=1` → `obligation_compare_count=0` |

---

### OB-04 — Obligation evidence gates too strict for marginal matches ⭐ P2

| Item | Detail |
|------|--------|
| **Where** | `evidence_sufficiency.py` `_hits_pass_gates` L62–78 · `catalog_matcher.py` L187–194 |
| **Thresholds** | `evidence_min_concept_overlap=0.25` · `routing_compare_min_confidence=0.85` · `evidence_min_score=0.35` |
| **Bug** | Token overlap between obligation text and hit passage below 0.25 → IPC even when retrieval ladder ran |
| **Scale** | ~10 obligations (after OB-01 unblocks funnel) |

---

## 4. Implementation phases (minimal, production-grade)

### OB-01 — Parallel-aware skip-resolved (P0, ~4 hours)

**Goal:** Never skip obligations in `parallel_hybrid` based on pre-compare section hits alone.

#### OB-01A — Config escape hatch (ship first, zero code risk)

```env
OBLIGATION_RETRIEVAL_SKIP_RESOLVED_SECTIONS=false
```

| File | Change |
|------|--------|
| `review_agent/.env.example` | Document flag; default `false` when `REVIEW_PIPELINE_MODE=parallel_hybrid` |
| `temp_java_sync/.env.example` | Same |

**Rollback:** `true`. **Cost:** +obligation MCP calls (capped by `MAX_OBLIGATIONS_PER_REVIEW`, ladder early-exit).

#### OB-01B — Minimal code (recommended production default)

**File:** `obligation_retrieval.py` — `should_skip_obligation_for_resolved_section`

```python
# After existing early returns, before hit-score check:
from review_agent.services.pipeline_mode import parallel_pipeline_active

if parallel_pipeline_active(tenant_id, settings):  # pass tenant_id into function
    return False
```

| Item | Detail |
|------|--------|
| **New param** | `tenant_id: str` on `should_skip_obligation_for_resolved_section` |
| **Call site** | `obligation_retrieval_nodes.py` L88 — pass `state["tenant_id"]` |
| **Serial hybrid** | Unchanged — serial runs obligation **before** section retrieval; `section_bundle` usually empty → skip rarely fires |
| **Optional config** | `obligation_skip_resolved_parallel_only: bool = True` — explicit kill switch |

**Alternative (stricter, defer):** Skip only if `section_compare_items` for `section_id` has COMPLIANT/NC — requires graph reorder; **not minimal**.

**Tests:**

- `tests/test_obligation_retrieval.py` — `test_skip_resolved_disabled_in_parallel_hybrid`
- Assert `section_skip_count == 0` in parallel wiring integration stub

**Pass criteria:**

- `obligation_retrieval_section_skip_count` → **0** (or ≪ 76)
- `skip_by_reason.section_path_resolved` → **0**
- `compare_queued` → **> 5** directional (429-independent)

---

### OB-02 — Section retrieval IPC: SR-01 + IPC-2 (P1, ~0.5 day)

**Goal:** `coverage_gate_ipc` ↓ ≥ 50%; `no_specific_category_overlap` ↓.

#### OB-02A — SR-01 (DONE — verify enabled)

Confirm env on all run paths (Dev UI, battery, platform):

```env
RETRIEVAL_MEANING_FIRST_ENABLED=true
RETRIEVAL_CATEGORY_HARD_FILTER=false
COMPARE_HIT_ALLOW_PRIMARY_FALLBACK=true
RETRIEVAL_SECTION_QUERY_MAX_CHARS=2000
```

**Harness:** `temp_java_sync/run_retrieval_ab_atlassian.py`

#### OB-02B — IPC-2 re-sync (index quality)

| Step | Action | File |
|------|--------|------|
| 1 | Re-sync Atlassian 9 policies with LLM tagger | `sync_service.py`, `fixtures/atlassian_e2e.json` |
| 2 | Fail battery if `weak_tag_count > 0` | `run_live_contract_battery.py` (Atlassian `validate_sync=True`) |
| 3 | Validate | `atlassian_ipc2.validate_policy_sync` → `[]` |

**Pass criteria:**

- `weak_tag_count=0` on sync preflight
- `coverage_gate_ipc` findings ↓ from ~11 toward ≤ 5
- No new false-NC on sections 15, 19, 20.4 (spot check)

**Do not:** Remove `apply_coverage_gate` or `is_incompatible_hit` globally.

---

### OB-03 — Validation fence: allow tenant-scoped policies (P0, ~2 hours)

**Goal:** `routing_validation_rejected` → 0 when policy is in request scope.

**File:** `routing_validation.py` L67–79

**Change (minimal):**

```python
# Before: reject if policy_id not in candidates
# After: reject only if outside candidates AND outside allowed_doc_ids
if (
    policy_id
    and candidates
    and policy_id not in candidates
    and policy_id not in allowed_doc_ids  # NEW: tenant registry still valid
    and item.status in (COMPLIANT, NON_COMPLIANT)
):
    ... no_invented_policies ...
```

| Keep strict | Soften |
|-------------|--------|
| Reject if `policy_id` not in `allowed_doc_ids` (tenant_doc_missing) | Allow scoped doc even if catalog fence missed it |
| Reject boilerplate NC, unused_policy_term | — |

**Tests:**

- `tests/test_routing_validation.py` (new or extend) — policy in `allowed_doc_ids` but not `candidates` → **passes**
- policy in neither → still IPC

**Pass criteria:**

- `routing_validation_rejected=0` when compare returns grounded NC/OK
- `obligation_compare_count` ≥ 1 on Atlassian smoke

**Optional (defer):** `EVIDENCE_COMPARE_ON_CATALOG_CANDIDATES=false` — broader, higher false-NC risk.

---

### OB-04 — Obligation evidence ladder tune (P2, ~2 hours)

**Goal:** Recover ~10 `low_concept_overlap` without opening floodgates.

**Apply only after OB-01 + OB-03 validated.**

| Setting | Current | OB-04 trial | Rationale |
|---------|---------|-------------|-----------|
| `EVIDENCE_MIN_CONCEPT_OVERLAP` | `0.25` | **`0.15`** | Token Jaccard on obligation↔hit; 0.25 too harsh for paraphrases |
| `ROUTING_COMPARE_MIN_CONFIDENCE` | `0.85` | **`0.75`** | More catalog `route_decision=compare` / `expand` |
| `EVIDENCE_MIN_SCORE` | `0.35` | keep | Don’t lower — reranker score already calibrated |
| `ROUTING_IPC_MAX_CONFIDENCE` | `0.60` | keep | Planner low-conf → IPC is correct |

**Files:**

- `review_agent/config.py` defaults (optional — prefer env-only trial)
- `review_agent/.env.example`, `temp_java_sync/.env.example`

**Code (optional minimal):** In `evaluate_evidence_sufficiency`, if `match.route_decision == "expand"` and `expand_round < max`, prefer expand over IPC — **already exists**; ensure `evidence_expand_max_rounds ≥ 1`.

**Tests:**

- `tests/test_evidence_sufficiency.py` — overlap 0.18 passes with new threshold

**Pass criteria:**

- `skip_by_reason.low_concept_overlap` ↓ ≥ 50%
- `obligation_ipc_rate` ↓ (not necessarily < 0.95 until 429 fixed)
- No increase in `routing_validation_rejected`

---

## 5. Production config profile (after OB-01–04)

```env
# Topology
REVIEW_PIPELINE_MODE=parallel_hybrid
OBLIGATION_ROUTING_ENABLED=true
OBLIGATION_ROUTING_TENANT_ALLOWLIST=

# OB-01 — parallel: do not skip obligations on pre-compare hits
OBLIGATION_RETRIEVAL_SKIP_RESOLVED_SECTIONS=false

# OB-02 — SR-01 (section retrieval)
RETRIEVAL_MEANING_FIRST_ENABLED=true
RETRIEVAL_CATEGORY_HARD_FILTER=false
COMPARE_HIT_ALLOW_PRIMARY_FALLBACK=true

# OB-04 — obligation evidence (after OB-01/03)
EVIDENCE_MIN_CONCEPT_OVERLAP=0.15
ROUTING_COMPARE_MIN_CONFIDENCE=0.75

# Caps unchanged (efficiency)
MAX_OBLIGATIONS_PER_REVIEW=80
MAX_PLANNER_CALLS_PER_REVIEW=60
OBLIGATION_RETRIEVAL_SECTION_HIT_REUSE=true
```

**Still strict (never remove):**

- `REVIEW_POLICY_SCOPE=request`
- `is_incompatible_hit` family blocks
- Quote grounding + guard pass
- `allowed_doc_ids` tenant fence (OB-03 keeps this)

---

## 6. Validation plan

| Step | Command / check | Metrics |
|------|-----------------|---------|
| 1 | Unit tests OB-01, OB-03, OB-04 | pytest green |
| 2 | `python run_retrieval_ab_atlassian.py` | `coverage_gate_ipc` ↓ |
| 3 | Atlassian only review (SR-01 + OB flags) | `section_path_resolved=0`, `compare_queued>5` |
| 4 | `run_battery_collect.py` (4 contracts) | NC vs baseline; IPC reason breakdown |
| 5 | Spot sections 15, 19, 20.4 | No false-NC from wrong policy family |

**Report script (new, minimal):** extend `_obligation_compare.py` or add `_ipc_reason_report.py`:

- Count IPC by `source` + `gap_type`
- Print `skip_by_reason`, `routing_validation_rejected`, `coverage_gate_ipc_count`

**Success (non-429 IPC only, before 429 fix):**

| Metric | Baseline | Target |
|--------|----------|--------|
| `section_path_resolved` skips | 76 | **0** |
| `coverage_gate_ipc` rows | ~11 | **≤ 5** |
| `routing_validation_rejected` | 1 | **0** |
| `low_concept_overlap` | 10 | **≤ 5** |
| `compare_queued` | 1 | **≥ 10** |

NC floor (≥6 Atlassian) likely needs **429 fix + above** together.

---

## 7. File touch list

| Phase | Files |
|-------|-------|
| **OB-01A** | `review_agent/.env.example`, `temp_java_sync/.env.example`, `temp_java_sync/.env` |
| **OB-01B** | `obligation_retrieval.py`, `obligation_retrieval_nodes.py`, `config.py` (optional flag), `tests/test_obligation_retrieval.py` |
| **OB-02A** | Verify SR-01 env; `run_retrieval_ab_atlassian.py` |
| **OB-02B** | `atlassian_ipc2.py`, `run_live_contract_battery.py`, fixtures sync |
| **OB-03** | `routing_validation.py`, `tests/test_routing_validation.py` |
| **OB-04** | `.env.example`, optional `config.py` defaults, `tests/test_evidence_sufficiency.py` |
| **Docs** | This plan, `plans/README.md` |

---

## 8. Risk matrix

| Risk | Mitigation |
|------|------------|
| OB-01 more LLM/MCP cost | Existing caps 80 obligations, planner 60, ladder early-exit |
| OB-03 wrong policy cited | Still require `allowed_doc_ids`; only widen vs catalog fence |
| OB-04 false obligation NC | Tune overlap only; keep incompatible guard |
| OB-02 false section NC | SR-01 incompatible guard + scoped policies |

---

## 9. Status checklist

- [x] **OB-01A** — `OBLIGATION_RETRIEVAL_SKIP_RESOLVED_SECTIONS=false` in env
- [x] **OB-01B** — `parallel_pipeline_active` guard in `should_skip_obligation_for_resolved_section`
- [x] **OB-02A** — SR-01 env on `review_agent/.env` + `temp_java_sync/.env`
- [ ] **OB-02B** — IPC-2 Atlassian re-sync, `weak_tag_count=0` (operator: re-sync + validate)
- [x] **OB-03** — `routing_validation.py` tenant-scoped pass-through
- [x] **OB-04** — `evidence_min_concept_overlap=0.15`, `routing_compare_min_confidence=0.75`
- [x] **Validation** — `_ipc_reason_report.py` + unit tests

**Verify:** `python _ipc_reason_report.py outputs/atlassian_review_live.json` after Atlassian run.

---

## 10. Direct answers

| Question | Answer |
|----------|--------|
| Biggest non-429 IPC? | **`section_path_resolved`** (~64 rows) |
| Minimal fix? | Disable skip in parallel (`false` env or OB-01B) |
| SR-01 enough for coverage_gate? | Helps; **IPC-2 tags** needed for full fix |
| Why compare_queued=1 but NC=0? | **OB-03** validation rejected the one item |
| Production order? | OB-01 → OB-03 → OB-02B → OB-04 → (then 429) |
