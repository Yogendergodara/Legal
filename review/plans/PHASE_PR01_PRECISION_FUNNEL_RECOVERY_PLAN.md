# Phase PR-01 — Precision Funnel Recovery (Post-OB, Excluding 429)

**Version:** 1.0  
**ID:** `DR-PHASE-PR01`  
**Parent:** [PHASE_OB01020304_NON429_IPC_RECOVERY_PLAN.md](./PHASE_OB01020304_NON429_IPC_RECOVERY_PLAN.md) · [PHASE_SR01_MEANING_FIRST_RETRIEVAL_PLAN.md](./PHASE_SR01_MEANING_FIRST_RETRIEVAL_PLAN.md) · [PHASE_R4_R5_IMPLEMENTATION_PLAN.md](./PHASE_R4_R5_IMPLEMENTATION_PLAN.md)  
**Out of scope:** HTTP 429 / `section_compare_failed` / key pool / quota (Phase B — fix separately)  
**Status:** **IMPLEMENTED** (PR-04/05/06/07 code + config; PR-02B re-sync manual; PR-04B semantic embed deferred)  
**Execution:** Operator + measured experiments → [PHASE_IPC3_PRODUCTION_EXECUTION_PLAN.md](./PHASE_IPC3_PRODUCTION_EXECUTION_PLAN.md)  
**Baseline:** Atlassian `atlassian_review_live.json` (2026-06-29, `parallel_hybrid`, OB-01 live)  
**Target:** Restore **≥4 NC Atlassian** (match `live_contract_battery_prev.json`) with **obligation_ipc_rate < 0.50** and **section confident compare > 50%** once 429 is controlled  

---

## 1. Executive summary

**Recall is largely fixed for sections** (`coverage_gate_ipc_count: 0` after SR-01). **Precision gates on the obligation funnel** still convert ~93% of obligations to IPC before compare runs. This is not “bad LLM accuracy” — it is **over-strict pre-compare filtering** using lexical overlap, catalog score thresholds, and planner confidence cuts.

Industry pattern (legal/compliance RAG products — Harvey-style, Kira-style, enterprise GRC copilots):

| Stage | Industry norm | Our gap |
|-------|---------------|---------|
| Recall | Hybrid dense+FTS, rerank, parent-child index | **OK** — hybrid + reranker shipped |
| Route | Semantic planner → scoped catalog fence | **Too many `routing_or_skip`** (~40) |
| Evidence | Rerank score + **semantic** overlap, not token Jaccard | **`low_concept_overlap`** (~24) — lexical gate |
| Compare | LLM judges with **full parent context** | Only **7** obligations compared |
| Index | LLM-tagged policy sections | **OB-02B not done** — weak tags on some policies |

**Principle:** *Let retrieval be broad; let compare LLM be the precision layer.* Pre-compare gates should block only **clear noise** (wrong tenant, boilerplate, incompatible policy family) — not paraphrase mismatch.

---

## 2. Baseline funnel (Atlassian LIVE — post OB-01)

```text
103 obligations extracted
  → compare_queued: 10
  → post_validation_compared: 7
  → obligation_ipc_findings: 96  (ipc_rate ≈ 0.93)

skip_by_reason (obligation evidence):
  routing_or_skip:     40   ← catalog/planner IPC before retrieval
  boilerplate:         24   ← classify/extract marked non-substantive
  low_concept_overlap: 24   ← lexical Jaccard gate (PR-04 target)
  evidence_sufficient: 10   ← passed gate → compare (good)
  low_relevance_score:  5

section path:
  coverage_gate_ipc_count: 0   ← SR-01 OK
  section_ipc_pct: 100%        ← dominated by 429 (out of scope here)
  obligation_retrieval_section_skip_count: 0  ← OB-01 OK
```

**What already shipped (do not re-implement):**

| ID | Status | Effect |
|----|--------|--------|
| OB-01 | **DONE** | `section_path_resolved` skip disabled in `parallel_hybrid` |
| OB-03 | **DONE** | Validation allows tenant-scoped policies outside catalog fence |
| OB-04 env | **DONE** | `EVIDENCE_MIN_CONCEPT_OVERLAP=0.15`, `ROUTING_COMPARE_MIN_CONFIDENCE=0.75` |
| SR-01 | **DONE** | Section meaning-first; `coverage_gate_ipc` → 0 |

---

## 3. Root-cause map (precision, ordered by impact)

```text
                    ┌─────────────────────────────────────┐
                    │  PR-05 routing_or_skip (~40)        │
                    │  catalog min_score + planner IPC    │
                    └──────────────┬──────────────────────┘
                                   ▼
                    ┌─────────────────────────────────────┐
                    │  PR-06 boilerplate (~24)            │
                    │  extract + planner confidence ≤0.3  │
                    └──────────────┬──────────────────────┘
                                   ▼
                    ┌─────────────────────────────────────┐
                    │  PR-03 retrieval recall (bounded)   │
                    │  union_top_k, expand rounds, chunks │
                    └──────────────┬──────────────────────┘
                                   ▼
                    ┌─────────────────────────────────────┐
                    │  PR-04 low_concept_overlap (~24) ⭐  │
                    │  token Jaccard in evidence_sufficiency │
                    └──────────────┬──────────────────────┘
                                   ▼
                         obligation compare LLM (7 today)
```

### PR-RC1 — `routing_or_skip` (catalog + planner funnel)

| Item | Detail |
|------|--------|
| **Where** | `catalog_matcher.py` L187–194 · `evidence_sufficiency.py` L108–122 · `semantic_routing_planner.md` L20–23 |
| **Mechanism** | `route_decision=ipc` when `top_score < catalog_match_min_score` (0.25) and no expand candidates; or `plan.confidence < routing_ipc_max_confidence` (0.60) |
| **Why strict** | Planner told to set confidence ≤0.3 for boilerplate; catalog search uses short queries capped at 200 chars; only 5 doc candidates max |
| **Production fix** | **Expand-first routing**: when `candidate_doc_ids` non-empty, prefer `expand` over `ipc`; widen catalog fence slightly inside tenant `allowed_doc_ids` only |

### PR-RC2 — `low_concept_overlap` (lexical evidence gate) ⭐

| Item | Detail |
|------|--------|
| **Where** | `evidence_sufficiency.py` `concept_overlap_score` L21–39 · `_hits_pass_gates` L74–75 |
| **Mechanism** | Token Jaccard (`[a-z0-9]{3,}`) between obligation+concepts vs `parent.title + parent.text` |
| **Why strict** | Legal paraphrase: contract says “notify within 72 hours”; policy says “without undue delay” → **0 overlap**, high rerank score ignored |
| **Production fix** | **Dual gate**: pass if `concept_overlap ≥ threshold` **OR** (`max_rerank_score ≥ evidence_min_score` AND `routing_confidence ≥ 0.65` AND candidate in fence). Optional: **embedding cosine** overlap (reuse existing embedder) |

### PR-RC3 — `boilerplate` skips (~24)

| Item | Detail |
|------|--------|
| **Where** | `section_classifier.py` · `obligation_extract` · `catalog_matcher.py` L106–112 · planner prompt |
| **Mechanism** | Sections/obligations marked non-substantive → `skipped_boilerplate` → IPC without retrieval |
| **Why mixed** | Some are truly boilerplate (correct skip); some are substantive obligations in boilerplate-looking sections (false skip) |
| **Production fix** | **Do not loosen globally.** PR-06: obligation-level re-check when section has `explicit_policy_mentions` or planner confidence > 0.5 |

### PR-RC4 — Index precision (tags + child recall)

| Item | Detail |
|------|--------|
| **Where** | `document_core/indexer/parent_child.py` · `CHILD_CHUNK_MAX_CHARS=700` · sync preflight `weak_tag_count` |
| **Mechanism** | Dense search runs on 700-char children; weak LLM/keyword tags hurt category layers (section path; obligation path uses catalog not tags) |
| **Production fix** | **OB-02B / IPC-2**: re-sync Atlassian with `tagger=llm`, `weak_tag_count=0`. **PR-03B**: optional `CHILD_CHUNK_MAX_CHARS=1000` + re-index (recall for long policy clauses) |

### PR-RC5 — `evidence_sufficient` (10) — mostly correct

These obligations **passed** gates and reached compare. Not a problem bucket. Target: grow this from 10 → 40+.

---

## 4. Implementation phases

### PR-00 — Preconditions & measurement (0.5 day)

**Goal:** Freeze baseline; confirm OB-01/SR-01 active; isolate non-429 metrics.

| Task | Action |
|------|--------|
| PR-00A | Run `temp_java_sync/_ipc_reason_report.py` on `outputs/atlassian_review_live.json` — export top-5 examples per `skip_by_reason` |
| PR-00B | Assert env on both `.env` files (see §8) |
| PR-00C | Add `engine_diagnosis.accuracy_paths.save.obligation_evidence_ipc` to golden gate thresholds |
| PR-00D | Document: **do not judge NC until 429 run is clean** — use `compare_queued`, `post_validation_compared`, `skip_by_reason` as leading indicators |

**Pass:** `obligation_retrieval_section_skip_count=0`, `coverage_gate_ipc_count=0`, report artifact committed to `outputs/`.

---

### PR-02 — OB-02B Index quality / IPC-2 (0.5 day, manual + script)

**Goal:** Policy index supports precision layers; weak tags = 0.

| Task | File / command |
|------|----------------|
| PR-02A | Run Atlassian sync for `atlassian-demo` / `e2e-demo` via existing sync harness |
| PR-02B | `python temp_java_sync/atlassian_ipc2.py` — assert `weak_tag_count=0`, `tagger=llm` for all 9 policies |
| PR-02C | `missing_atlassian_refs()` → empty |
| PR-02D | Re-run section A/B: `run_retrieval_ab_atlassian.py` — confirm `coverage_gate_ipc` stays 0 |

**Pass:** `validate_policy_sync()` returns `[]`.  
**Rollback:** Re-sync from last good `sync_atlassian_e2e-demo.json` snapshot.

---

### PR-05 — Routing funnel: reduce `routing_or_skip` (1 day)

**Goal:** `routing_or_skip` 40 → **< 15** without opening cross-tenant policy invention.

#### PR-05A — Expand-first catalog routing (code)

**File:** `review_agent/services/catalog_matcher.py`

```python
# After capped candidates computed:
if capped and top_score < cfg.catalog_match_min_score:
    route_decision = "expand"  # today: ipc unless evidence_compare_on_catalog_candidates
elif capped and top_score >= cfg.catalog_match_min_score * 0.85:
    route_decision = "compare"  # marginal catalog hit still fenced
```

| Config | Default | PR-05 |
|--------|---------|-------|
| `catalog_match_min_score` | 0.25 | keep |
| `evidence_compare_on_catalog_candidates` | true | keep **true** |
| `catalog_match_max_candidates` | 5 | **8** (tenant-fenced) |

#### PR-05B — Planner confidence floor for fenced candidates (code)

**File:** `review_agent/services/evidence_sufficiency.py`

When `match.candidate_doc_ids` non-empty and `plan.confidence < routing_ipc_max_confidence`:

- Today → IPC via `low_routing_confidence` path in some branches
- **Change:** route to `expand` retrieval round instead of immediate IPC if `top_score > 0` or alias hit

#### PR-05C — Catalog query budget (config)

```env
CATALOG_MATCH_MAX_QUERIES=4          # already default
CATALOG_MATCH_TOP_K=12               # was 8
MAX_CATALOG_SEARCH_CALLS_PER_REVIEW=150  # was 120
```

**Tests:** `tests/test_catalog_matcher.py` — marginal score → `expand` not `ipc`; tenant fence still rejects foreign doc_ids.

**Pass:** `skip_by_reason.routing_or_skip` < 15 on Atlassian smoke.

---

### PR-04 — Semantic evidence gate (replace lexical-only overlap) (1.5 days) ⭐

**Goal:** `low_concept_overlap` 24 → **< 8**; `compare_queued` 10 → **≥ 35**.

#### PR-04A — Rerank-score bypass (minimal, ship first)

**File:** `review_agent/services/evidence_sufficiency.py` — `_hits_pass_gates`

```python
def _hits_pass_gates(...):
    base_pass = ...  # existing checks
    if base_pass:
        return True
    # PR-04: high rerank + fenced candidate → defer precision to compare LLM
    if (
        cfg.evidence_rerank_bypass_enabled
        and max_score >= cfg.evidence_min_score
        and concept_overlap >= cfg.evidence_min_concept_overlap * 0.5
        and plan.confidence >= cfg.evidence_rerank_bypass_min_confidence
    ):
        return True
    return False
```

**New config:**

```env
EVIDENCE_RERANK_BYPASS_ENABLED=true
EVIDENCE_RERANK_BYPASS_MIN_CONFIDENCE=0.55
```

**Rationale:** Production systems use **cross-encoder score as primary evidence**; lexical overlap is a tie-breaker, not a hard veto.

#### PR-04B — Semantic concept overlap (optional phase 2)

**File:** new `review_agent/services/concept_overlap.py`

- Embed `obligation.text + plan.intent` vs each hit `parent.text` (first 1500 chars)
- `semantic_overlap = max(cosine_sim)`
- Pass if `semantic_overlap ≥ 0.72` OR lexical pass

Reuse `document_core` embedding client (same as hybrid search). Cache per obligation_id in review scope.

**Config:**

```env
EVIDENCE_SEMANTIC_OVERLAP_ENABLED=true
EVIDENCE_MIN_SEMANTIC_OVERLAP=0.72
```

#### PR-04C — Expand round broadening (config)

```env
EVIDENCE_EXPAND_MAX_ROUNDS=2           # was 1
EVIDENCE_EXPAND_BROADEN_MODE=both      # concepts + catalog_neighbor
EVIDENCE_EXPAND_MAX_EXTRA_DOCS=3       # was 2
```

**Tests:** `tests/test_evidence_sufficiency.py` — paraphrase pair with high rerank, low lexical → `decision=compare`.

**Pass:** `low_concept_overlap` < 8; `post_validation_compared` ≥ 20 (429-free run).

---

### PR-03 — Retrieval recall: more context per obligation (1 day)

**Goal:** Obligation retrieval returns richer passages before gates run.

#### PR-03A — Union top-K and query ladder (config)

```env
OBLIGATION_RETRIEVAL_UNION_TOP_K=20      # was 12
OBLIGATION_RETRIEVAL_MAX_QUERIES=4       # was 3
RETRIEVAL_RECALL_TOP_K=30                # section path; was 20
RETRIEVAL_FINAL_TOP_K=12                 # was 10
```

**Cost:** +MCP calls bounded by `OBLIGATION_RETRIEVAL_ADAPTIVE_LADDER=true` early-exit.

#### PR-03B — Child chunk sizing (index — requires re-sync)

**File:** `document_core/.env`

```env
CHILD_CHUNK_MAX_CHARS=1000               # was 700
CHILD_CHUNK_OVERLAP_SENTENCES=3          # was 2
```

Re-index Atlassian policies after change. Parent chunks unchanged (full section text); dense recall improves for long clauses.

#### PR-03C — Neighbor chunk expansion (code, optional)

**File:** `obligation_retrieval.py`

When expand_mode and hit is child-backed: fetch adjacent child chunks from same parent (`section_id` siblings) and merge into `policy_hits` before evidence gate.

**Pattern:** Parent-child RAG (LlamaIndex / LangChain multi-vector) — retrieve child, **pass parent + neighbors** to compare.

**Tests:** synthetic obligation where key phrase spans child boundary → hit after neighbor expand.

---

### PR-06 — Boilerplate precision (0.5 day)

**Goal:** Cut false boilerplate IPC without disabling true boilerplate skips.

| Task | Detail |
|------|--------|
| PR-06A | **Obligation extract:** skip obligations in sections with `classify_warning=boilerplate_skip` only when obligation has no `explicit_policy_mentions` |
| PR-06B | **Planner:** if `explicit_policy_mentions` non-empty, floor confidence at **0.55** (override ≤0.3 rule in prompt via post-process) |
| PR-06C | Keep `routing_ipc_max_confidence=0.60` — do not lower globally |

**Pass:** `boilerplate` skip count stable or ↓; no new false-NC on sections 15, 19, 20.4 (spot check).

---

### PR-07 — Compare context envelope (0.5 day)

**Goal:** When compare runs, LLM sees enough policy text for accurate NC.

```env
OBLIGATION_COMPARE_MAX_OBLIGATION_CHARS=3000   # was 2000
PLAYBOOK_COMPARE_MAX_CHARS=2000                # was 1500
COMPARE_MAX_POLICY_HITS=3                      # align temp + review_agent
```

**File (optional):** `section_compare_llm.py` / `obligation_compare_llm.py` — include `parent.context_text` + breadcrumb in prompt block.

**Guard:** `COMPARE_TOKEN_BUDGET_MODE=aligned` stays on; batch splitter handles overflow.

---

### PR-08 — Validation & battery (0.5 day)

| Gate | Threshold |
|------|-----------|
| `obligation_ipc_rate` | < 0.50 |
| `post_validation_compared` | ≥ 20 |
| `compare_queued` | ≥ 35 |
| `routing_or_skip` | < 15 |
| `low_concept_overlap` | < 8 |
| Atlassian NC | ≥ 4 (with 429 fixed) |
| False-NC regression | sections 15, 19, 20.4 unchanged |

**Commands:**

```bash
cd Legal/temp_java_sync
python run_retrieval_ab_atlassian.py
python _ipc_reason_report.py outputs/atlassian_review_live.json
python run_battery_collect.py
```

---

## 5. Production design patterns (why this matches real products)

| Pattern | Product norm | PR-01 implementation |
|---------|--------------|----------------------|
| **Two-stage retrieval** | Broad recall → rerank top-N | Already shipped; PR-03 widens N |
| **Scoped fence** | Never compare outside tenant index | Keep `allowed_doc_ids` — all relaxations stay inside fence |
| **LLM-as-judge** | Gates block noise, not paraphrase | PR-04 rerank bypass + semantic overlap |
| **Parent-child index** | Embed children, read parents | PR-03B chunk size + PR-03C neighbor expand |
| **INCONCLUSIVE ≠ COMPLIANT** | IPC when evidence truly absent | Keep IPC for empty fence / incompatible family |
| **Index quality** | Tags assist, not veto semantics | PR-02 IPC-2 re-sync |

**Anti-patterns (do NOT do):**

- Remove `policy_coverage` / incompatible-family guard → false NC from wrong policy family
- Set `EVIDENCE_MIN_CONCEPT_OVERLAP=0` globally → obligation compare spam, false NC
- Disable boilerplate skips entirely → wasted LLM on notices/severability
- Increase batch concurrency to “go faster” → more 429, worse accuracy

---

## 6. Fix order (strict)

```text
1. PR-00  Measure baseline + confirm OB-01/SR-01 env
2. PR-02  IPC-2 re-sync (weak_tag_count=0)
3. PR-05  routing_or_skip (catalog expand-first)
4. PR-04  evidence gates (rerank bypass → semantic overlap)
5. PR-03  retrieval recall (top_k, chunks, neighbors)
6. PR-06  boilerplate precision
7. PR-07  compare context envelope
8. PR-08  Battery vs live_contract_battery_prev.json
```

**Parallel track (user):** Phase B 429 — without it, section NC stays 0 regardless of PR-01.

---

## 7. Why past env changes did not improve accuracy

| Change | Expected | Actual reason |
|--------|----------|---------------|
| SR-01 | More section compares | Section **coverage** fixed; **429** blocked compare LLM |
| OB-01 | More obligation compares | `compare_queued` 1→10 ✓; still **93% IPC** from PR-RC1/2/3 |
| OB-04 overlap 0.15 | Fewer `low_concept_overlap` | Lexical gate still fails on paraphrase — need PR-04 |
| `parallel_hybrid` | Faster | More parallel LLM → **429** → slower + more IPC |
| Key pool placeholders | Fewer 429 | **Invalid keys** — no effect |

**Accuracy (NC) only increases when `section_compare_llm` and `obligation_compare_llm` **complete successfully** with grounded quotes.

---

## 8. Required env (both `review_agent/.env` + `temp_java_sync/.env`)

### Already required (verify)

```env
REVIEW_POLICY_SCOPE=request
REVIEW_PIPELINE_MODE=parallel_hybrid
OBLIGATION_ROUTING_ENABLED=true
OBLIGATION_ROUTING_TENANT_ALLOWLIST=
OBLIGATION_RETRIEVAL_SKIP_RESOLVED_SECTIONS=false
OBLIGATION_SKIP_RESOLVED_PARALLEL_GUARD=true

RETRIEVAL_MEANING_FIRST_ENABLED=true
RETRIEVAL_CATEGORY_HARD_FILTER=false
RETRIEVAL_SECTION_QUERY_MAX_CHARS=2000
COMPARE_HIT_ALLOW_PRIMARY_FALLBACK=true

EVIDENCE_MIN_CONCEPT_OVERLAP=0.15
ROUTING_COMPARE_MIN_CONFIDENCE=0.75
EVIDENCE_COMPARE_ON_CATALOG_CANDIDATES=true
```

### Add for PR-01 rollout

```env
# PR-05
CATALOG_MATCH_TOP_K=12
CATALOG_MATCH_MAX_CANDIDATES=8

# PR-04 (after code deploy)
EVIDENCE_RERANK_BYPASS_ENABLED=true
EVIDENCE_RERANK_BYPASS_MIN_CONFIDENCE=0.55
EVIDENCE_EXPAND_MAX_ROUNDS=2
EVIDENCE_EXPAND_BROADEN_MODE=both

# PR-03
OBLIGATION_RETRIEVAL_UNION_TOP_K=20
OBLIGATION_RETRIEVAL_MAX_QUERIES=4

# PR-07
OBLIGATION_COMPARE_MAX_OBLIGATION_CHARS=3000
PLAYBOOK_COMPARE_MAX_CHARS=2000
COMPARE_MAX_POLICY_HITS=3
```

### document_core/.env (PR-03B — after re-sync)

```env
CHILD_CHUNK_MAX_CHARS=1000
CHILD_CHUNK_OVERLAP_SENTENCES=3
```

---

## 9. Files touched (summary)

| Phase | Files |
|-------|-------|
| PR-04 | `evidence_sufficiency.py`, `config.py`, `tests/test_evidence_sufficiency.py` |
| PR-05 | `catalog_matcher.py`, `evidence_sufficiency.py`, `tests/test_catalog_matcher.py` |
| PR-03 | `obligation_retrieval.py`, `document_core/.env`, optional neighbor helper |
| PR-06 | `obligation_extract.py`, `semantic_routing_planner.py` |
| PR-07 | `obligation_compare_llm.py`, `config.py` |
| PR-02 | `temp_java_sync/atlassian_ipc2.py`, sync outputs |
| PR-08 | `bootstrap_env.py` (optional PR defaults), `_ipc_reason_report.py` |

---

## 10. Success metrics (429-free Atlassian run)

| Metric | Baseline (2026-06-29) | PR-01 target |
|--------|----------------------|--------------|
| `obligation_ipc_rate` | 0.93 | **< 0.50** |
| `compare_queued` | 10 | **≥ 35** |
| `post_validation_compared` | 7 | **≥ 20** |
| `routing_or_skip` | 40 | **< 15** |
| `low_concept_overlap` | 24 | **< 8** |
| `coverage_gate_ipc_count` | 0 | **0** (hold) |
| `section_path_resolved` skips | 0 | **0** (hold) |
| NC violations | 0 (429) | **≥ 4** |
| Wall time | ~600s | **≤ 650s** (more compares OK) |

---

## 11. Rollback

Each phase is independently rollbackable via env:

| Phase | Rollback |
|-------|----------|
| PR-04 | `EVIDENCE_RERANK_BYPASS_ENABLED=false` |
| PR-05 | `CATALOG_MATCH_MAX_CANDIDATES=5` |
| PR-03 | revert top_k + re-index with `CHILD_CHUNK_MAX_CHARS=700` |
| PR-06 | revert extract/planner post-process |
| Full | `OBLIGATION_ROUTING_ENABLED=false`, `REVIEW_PIPELINE_MODE=serial` |

---

## 12. FAQ

**Q: Recall is good — why still IPC?**  
A: Obligation path uses **different gates** than section path. SR-01 fixed section tags; obligations fail on **catalog scores + lexical overlap**.

**Q: Bigger chunks?**  
A: Parent text is already full section. Increase **child chunk size** (PR-03B) and **union_top_k** so dense search surfaces the right passage.

**Q: Will precision fixes hurt accuracy?**  
A: Fenced rerank-bypass only allows compare when **tenant-scoped hits score ≥ 0.35**. Compare LLM + quote validate + guard pass remain the precision layer.

**Q: Why doesn't time decrease?**  
A: PR-01 **adds** justified compare work. Time drops only after 429 is fixed (less backoff sleep), not by skipping compares.
