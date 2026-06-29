# Phase SR-01 — Meaning-First Retrieval & Precision Tuning

**Version:** 1.0  
**ID:** `DR-PHASE-SR01`  
**Parent:** [PHASE_IPC_REMEDIATION_MASTER_PLAN.md](./PHASE_IPC_REMEDIATION_MASTER_PLAN.md) · [PHASE_RC141516_QUOTE_IPC_CLARIFICATION_PLAN.md](./PHASE_RC141516_QUOTE_IPC_CLARIFICATION_PLAN.md) · [PHASE10_SECTION_FIRST_RETRIEVAL_PLAN.md](./PHASE10_SECTION_FIRST_RETRIEVAL_PLAN.md) · [PHASE_R_SEMANTIC_ROUTING_PLAN.md](./PHASE_R_SEMANTIC_ROUTING_PLAN.md)  
**Targets:** Real retrieval IPC (`coverage_gate_ipc`, `no_specific_category_overlap`, `no_policy`) — **not** 429 / `section_compare_failed` IPC  
**Status:** **IMPLEMENTED** (SR1 + SR3-A/B minimal; SR2/4 harness ready)  
**Scope:** Shift retrieval from **tag-first precision** to **meaning-first recall + tag-second precision** — without new search infrastructure, without lowering compare evidence gates, without increasing wall time on successful full-funnel runs  
**Effort:** ~0.5 day (config + A/B harness) + ~1 day (IPC-2 tags) + ~1 day (query + retry-softening code)  
**Risk:** Medium on global `all_top_k` — mitigated by scoped policies + incompatible-family guard + staged rollout

---

## 1. Problem statement

Semantic search **already exists** (`SEARCH_BACKEND=hybrid`, cross-encoder reranker). Atlassian battery shows **~37% of IPC is “real retrieval”** — hits existed or hybrid recall worked, but **tag/precision layers vetoed them** before compare LLM ran.

| IPC bucket | Typical reason | Fix layer |
|------------|----------------|-----------|
| **429 / compare_failed** | Mistral quota; compare never finishes | Phase B / RC-14 — **not SR-01** |
| **coverage_gate_ipc** | `no_specific_category_overlap`, weak policy tags | **SR-01 + IPC-2** |
| **no_policy** | Pre-filter shrunk corpus to zero docs | **SR-01 SR1** |
| **playbook_compare IPC** | Wrong policy family reached compare | Keep incompatible guard; tune hit selection |

### Evidence (Atlassian LIVE, obligation ON)

| Signal | Value | Implication |
|--------|-------|-------------|
| `SEARCH_BACKEND` | `hybrid` in `document_core/.env` | Dense + FTS recall available |
| Section IPC % | 92–100% | Compare rarely produces NC |
| `coverage_gate_ipc` | ~21% of IPC rows | Post-retrieval tag gate, not “no hits” |
| `no_specific_category_overlap` | dominant coverage reason | Semantic hit rejected for tag mismatch |
| Obligation `compare_queued` | 0–1 | Obligation path blocked separately (RC-03/08) |
| NC vs previous hybrid | 4 NC → 0 NC | Precision + 429 compound; SR-01 addresses precision half |

**Misread to avoid:** “We need a new vector DB.” → **No.** Need to stop weak tags from vetoing good hybrid hits.

---

## 2. Architecture today (two layers)

```text
┌─────────────────────────────────────────────────────────────────┐
│  RECALL (meaning) — already shipped                              │
│  multi_retrieve_for_section()                                    │
│    ├─ dense: search_policy_recall (embeddings when hybrid)       │
│    ├─ fts:   search_policy_fts                                   │
│    └─ meta:  search_policy_by_categories (tag sweep)              │
│  → union → diverse_top_k → rerank_hits (cross-encoder)            │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  PRECISION (tags) — too aggressive today                         │
│  1. retrieval_category_hard_filter → doc_id pre-limit             │
│  2. retrieval_relevance_gate → filter_hits_by_relevance         │
│  3. apply_coverage_gate → no_specific_category_overlap → IPC      │
│  4. select_compare_hits (category_aligned) → drop non-overlap     │
│  5. section_compare LLM                                         │
└─────────────────────────────────────────────────────────────────┘
```

**Root cause (ordered):**

| ID | Root cause | Where | Why it hurts |
|----|------------|-------|--------------|
| **SR-RC1** | **Pre-search doc filter** limits corpus to tag-matched policies before dense/FTS run | `multi_retrieval.py` `_resolve_filter_document_ids` L151–221 · `config.py` `retrieval_category_hard_filter=True` | Correct policy never searched if policy section tags are weak/wrong |
| **SR-RC2** | **Query too narrow** — attempt 0 uses `query_terms[0]` only, not full section body | `multi_retrieval.py` `_query_for_attempt` L110–148 | Dense embedding under-represents section meaning |
| **SR-RC3** | **Coverage gate requires specific category overlap** on hits | `policy_coverage.py` `validate_section_coverage` · `retrieval_relevance.py` `has_specific_category_overlap` L72–85 · `policy_coverage_require_specific_overlap=True` | Reranked hit with high semantic score discarded when tags don’t share a specific category |
| **SR-RC4** | **Compare hit mode `category_aligned`** second-filters same overlap | `compare_hit_selection.py` `select_compare_hits` L67–87 | Double tag filter after coverage gate |
| **SR-RC5** | **Weak policy index tags** (IPC-2) | Sync preflight `weak_tag_count`, `atlassian_ipc2.py` | Precision layer correctly rejects — but tags are wrong, so good text match fails overlap |
| **SR-RC6** | **429 IPC masks SR-01 gains** | `compare_failure_status.py`, section compare | Fixing retrieval alone won’t restore NC if compare LLM never runs |

**SR-01 fixes SR-RC1–SR-RC5.** SR-RC6 remains Phase B / quota work.

---

## 3. Design principle — meaning-first, tag-second

| Layer | Goal | Keep strict | Soften (SR-01) |
|-------|------|-------------|----------------|
| **Scope** | Only tenant/request policies | `scope_document_ids`, discovery cap | — |
| **Recall** | Maximize relevant chunks by meaning | hybrid + reranker | Full section text query; `category_hard_filter=false` |
| **Safety** | Block cross-family compares | `is_incompatible_hit` (governing_law vs incident) | — |
| **Precision** | Right policy family | incompatible guard, scoped policies | Overlap as **soft boost**, not hard veto on retry |
| **Compare** | Grounded NC/OK | quote validate, guard pass | — |

**Efficiency:** Softer pre-filter → **fewer retrieval retry attempts** and fewer wasted compare skips. IPC-2 fixes tags once → less ladder churn. **No extra LLM calls** in SR1 (config-only).

---

## 4. What NOT to do

| Action | Why avoid |
|--------|-----------|
| Remove `apply_coverage_gate` entirely | False NC from privacy vs liability families |
| `COMPARE_POLICY_HIT_MODE=all_top_k` globally without scope | Wrong policy cited; audit noise |
| Lower `retrieval_relevance_min_score` to “fix IPC” | Admits junk hits; compare cost ↑ |
| Disable reranker | Loses best existing semantic signal |
| Replace pgvector / new search stack | Already hybrid; problem is gates after search |

---

## 5. Implementation phases

### SR1 — Config experiment (no code, ~2 hours + 1 Atlassian run)

**Goal:** Prove `coverage_gate_ipc` ↓ and NC ↑ on one contract without false-NC regression.

| Setting | Current (prod-like) | SR1 trial | Effect |
|---------|---------------------|-----------|--------|
| `RETRIEVAL_CATEGORY_HARD_FILTER` | `true` | **`false`** | Search all scoped policies by meaning; tags don’t pre-shrink corpus |
| `RETRIEVAL_SKIP_HARD_FILTER_FOR_GENERAL` | `true` | keep | Already skips for general sections |
| `COMPARE_POLICY_HIT_MODE` | `category_aligned` | keep initially | Avoid global all_top_k in SR1 |
| `COMPARE_HIT_ALLOW_PRIMARY_FALLBACK` | `false` | **`true`** | If overlap filter empty, send top reranked hit to compare |
| `RETRIEVAL_COVERAGE_FILTER_ALIGNED` | `true` | keep | Still strip incompatible families |
| `POLICY_COVERAGE_REQUIRE_SPECIFIC_OVERLAP` | `true` | keep in SR1 | Loosen in SR2 only after metrics |
| `SECTION_CLASSIFY_MODE` | `lexical_first` | keep | LLM classify deferred to SR3 |

**Files:**

- `Legal/review/review_agent/.env.example` — document SR1 block
- `Legal/temp_java_sync/.env` + `.env.example` — golden trial flags
- `Legal/document_core/.env` — confirm `SEARCH_BACKEND=hybrid`, `RERANKER_ENABLED=true` (no change)

**Harness (new):**

- `temp_java_sync/run_retrieval_ab_atlassian.py` — runs Atlassian only; emits before/after:
  - `coverage_gate_ipc_count`
  - `no_policy` / `no_specific_category_overlap` counts
  - NC violations, false-NC spot check (sections 15, 19, 20.4)
  - wall time, retrieval attempt count

**Pass criteria (SR1):**

- `coverage_gate_ipc_count` ↓ ≥ 30% vs baseline
- NC violations ≥ 2 (directional; full gate still needs 429 fix)
- Zero new false-NC on governing-law / notice sections (manual spot check)
- Wall time ≤ baseline × 1.05 on successful run

**Rollback:** Revert two env keys.

---

### SR2 — IPC-2 index quality (fixes root cause, ~1 day)

**Goal:** Fix **SR-RC5** so precision layer stops blocking semantically correct hits.

| Task | Where | How |
|------|-------|-----|
| Re-sync Atlassian policies with LLM tagger | `temp_java_sync/sync_service.py`, fixtures `atlassian_e2e.json` | `replace_policies=True`; `validate_policy_sync` → `weak_tag_count=0` |
| Block battery on weak tags | `run_live_contract_battery.py` (already optional); enable for Atlassian | Fail fast before accuracy measurement |
| Per-section policy categories on chunks | `document_core` ingest / tagger pipeline | Ensure chunk `metadata.categories` align with section topic |
| Catalog doc categories for overlap | `policy_coverage.catalog_doc_categories` | Doc-level tags supplement weak chunk tags |

**Files:** `temp_java_sync/atlassian_ipc2.py` · `PHASE_IPC2` (when present) · ingest tagger config

**Pass criteria:**

- `weak_tag_count=0` on Atlassian sync preflight
- `no_specific_category_overlap` IPC ↓ ≥ 50% vs SR1-without-IPC2
- Same scoped policy set (9 Atlassian policies)

**Why before SR3 code:** Cheaper than softening gates globally; restores precision without sacrificing safety.

---

### SR3 — Query & retry-softening code (~1 day)

**Goal:** Meaning-first recall in code; precision softens **only on retry**, not attempt 0.

#### SR3-A — Full section text as dense query

| Item | Detail |
|------|--------|
| **What** | Attempt 0 dense/FTS query = trimmed section body (cap ~2k chars), not `query_terms[0]` |
| **Where** | `multi_retrieval.py` `_query_for_attempt` |
| **Why** | Embeddings match obligation/section semantics; first term is often too narrow |
| **How** | `query = section.text[:cfg.retrieval_section_query_max_chars]` with title prefix; keep `query_terms` for attempt 1+ |
| **Config** | `retrieval_section_query_max_chars: int = 2000` in `config.py` |

#### SR3-B — Precision ladder on retry only

| Attempt | `category_hard_filter` | `require_specific_overlap` | `compare_hit_mode` |
|---------|------------------------|------------------------------|---------------------|
| 0 | `false` if `retrieval_meaning_first_enabled` | `true` | `category_aligned` |
| 1 | `false` | `false` | `category_aligned` + `compare_hit_allow_primary_fallback=true` |
| 2 | `false` | `false` | trust reranker top-k (`compare_hit_trust_retrieval_gate`) |

| Item | Detail |
|------|--------|
| **Where** | `multi_retrieve_for_section`, `section_compare_nodes.py` (pass `attempt_index` to coverage/hit selection) |
| **Why** | Attempt 0 stays safe; retries rescue tag-mismatch without global all_top_k |
| **Config** | `retrieval_meaning_first_enabled: bool = True` (feature flag) |

#### SR3-C — Category soft boost (optional, if SR1+2 insufficient)

| Item | Detail |
|------|--------|
| **What** | In `score_hit_relevance`, add +0.1 boost when `has_specific_category_overlap`; do not zero score when absent |
| **Where** | `retrieval_relevance.py` `score_hit_relevance` |
| **Why** | Tags inform ranking, not binary admission |

**Tests:**

- `tests/test_multi_retrieval.py` — attempt 0 query uses section text when flag on
- `tests/test_double_filter_alignment.py` — retry attempt relaxes overlap
- `tests/test_policy_coverage.py` — incompatible hits still blocked

---

### SR4 — Validation & production defaults (~0.5 day)

| Task | Detail |
|------|--------|
| Full 4-contract battery | `run_battery_collect.py` with SR1+SR2+SR3 flags |
| Compare script | Extend `_obligation_compare.py` → `_retrieval_ab_report.py` with IPC reason breakdown |
| `engine_diagnosis` | Ensure `coverage_gate_ipc_count`, `retrieval_filter_meta` per section exported |
| `config_advisory.py` | Warn if `category_hard_filter=true` + `weak_tag_count>0` on tenant |
| Production default | After pass: `retrieval_meaning_first_enabled=true`, `retrieval_category_hard_filter=false`, overlap required only on attempt 0 |

**Golden thresholds (no lowering):**

- Keep `min_violations` floors
- Add **ceiling** optional: `max_coverage_gate_ipc_pct` for regression guard

---

## 6. Config reference (optimized real-world profile)

**Production target** after SR1–SR4 validated:

```env
# document_core — unchanged
SEARCH_BACKEND=hybrid
RERANKER_ENABLED=true

# review_agent — meaning-first recall
RETRIEVAL_MEANING_FIRST_ENABLED=true
RETRIEVAL_CATEGORY_HARD_FILTER=false
RETRIEVAL_SKIP_HARD_FILTER_FOR_GENERAL=true
RETRIEVAL_COVERAGE_FILTER_ALIGNED=true
RETRIEVAL_SECTION_QUERY_MAX_CHARS=2000

# precision — tag-second, not tag-only
COMPARE_POLICY_HIT_MODE=category_aligned
COMPARE_HIT_ALLOW_PRIMARY_FALLBACK=true
COMPARE_HIT_TRUST_RETRIEVAL_GATE=true
POLICY_COVERAGE_REQUIRE_SPECIFIC_OVERLAP=true   # attempt 0 only when meaning_first on
POLICY_COVERAGE_ENABLED=true

# scope — stay strict
REVIEW_POLICY_SCOPE=request
REVIEW_REJECT_INLINE_POLICIES=true
```

**Still strict (never soften):**

- `is_incompatible_hit` pairings (`retrieval_relevance.py` L26–31)
- Request-scoped `policy_document_ids`
- Compare quote grounding + guard pass
- Reranker fusion

---

## 7. Success metrics

| Metric | Baseline (Atlassian) | SR1 target | SR2+SR3 target |
|--------|----------------------|------------|----------------|
| `coverage_gate_ipc_count` | high | −30% | −50% |
| `no_specific_category_overlap` rows | dominant | −30% | −70% |
| `no_policy` IPC | ~2–6% | −50% | −80% |
| NC violations | 0–4 (429-dependent) | ≥2 | ≥6 (with 429 fix) |
| False NC (wrong policy) | spot-check 0 | 0 | 0 |
| Retrieval attempts / section | ~1–3 | ≤2 avg | ≤1.5 avg |
| Wall time (full funnel success) | ~300–680s | ≤ baseline | ≤ baseline |

**IPC interpretation (Phase G):** SR-01 improvements count toward **real retrieval IPC** reduction only; 429 IPC tracked separately in `baseline_interpretation`.

---

## 8. Dependency order (critical path)

```text
Phase B / 429 fix (parallel, highest NC leverage)
        │
        ▼
SR1 config trial (Atlassian A/B) ──► proves precision hypothesis
        │
        ▼
SR2 IPC-2 re-sync (weak tags) ──► fixes root cause without unsafe global all_top_k
        │
        ▼
SR3 query + retry ladder ──► production-grade meaning-first
        │
        ▼
SR4 full battery + prod defaults
```

**Obligation routing (RC-08):** Independent track. SR-01 helps **section** path; obligation path already has semantic planner + `obligation_retrieval_section_hit_reuse` — enable hit reuse after SR1 proves section retrieval quality.

---

## 9. Risk matrix & rollback

| Risk | Likelihood | Mitigation | Rollback |
|------|------------|------------|----------|
| False NC (wrong policy family) | Medium if `all_top_k` global | Scoped policies + incompatible guard; staged SR1 | `category_hard_filter=true` |
| Compare cost ↑ (more items) | Low | `compare_max_policy_hits=3` unchanged | — |
| Latency ↑ | Low | Fewer retries; cache unchanged | Disable `meaning_first` flag |
| Tags still wrong after SR2 | Medium | SR2 gate blocks release | IPC-2 manual tag fix per policy |

---

## 10. File touch list

| Phase | Files |
|-------|-------|
| **SR1** | `review_agent/.env.example`, `temp_java_sync/.env.example`, `run_retrieval_ab_atlassian.py` (new) |
| **SR2** | `atlassian_ipc2.py`, `sync_service.py`, fixtures, ingest tagger |
| **SR3** | `multi_retrieval.py`, `policy_coverage.py`, `compare_hit_selection.py`, `config.py`, `section_compare_nodes.py`, tests |
| **SR4** | `config_advisory.py`, `engine_diagnosis.py`, `_retrieval_ab_report.py`, `golden_thresholds.json` (optional ceiling), `plans/README.md` |

---

## 11. Direct answers

| Question | Answer |
|----------|--------|
| Can we search by meaning not tags? | **Yes — already partial**; SR-01 completes the shift |
| Should we lower precision? | **Yes, selectively** — pre-filter and overlap veto, not scope or incompatible guard |
| Will it fix IPC? | **Yes for real retrieval IPC**; **no** for 429/compare_failed |
| Accurate **and** efficient? | **Yes** — meaning-first reduces retry ladder + wasted compare skips; IPC-2 fixes tags once |
| Best first step? | **SR1** on Atlassian: `retrieval_category_hard_filter=false` + `compare_hit_allow_primary_fallback=true` |

---

## 12. Status checklist

- [x] **SR1** — Config + `apply_sr01_retrieval_defaults()` + `run_retrieval_ab_atlassian.py`
- [ ] **SR2** — IPC-2 re-sync, `weak_tag_count=0`
- [x] **SR3-A** — Section body query (`retrieval_meaning_first_enabled`)
- [x] **SR3-B** — Retry overlap relax + coverage primary fallback (minimal)
- [ ] **SR4** — Full battery validation + prod defaults after A/B pass

**Run A/B:** `python run_retrieval_ab_atlassian.py` (optional `SR01_AB_MODE=full` for side-by-side baseline)
