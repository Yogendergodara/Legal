# Phase 10B — Section-First LLM Review (Plan 2 of Phase 10)

**Plan ID:** `DR-PHASE-10B`  
**Parent:** [PHASE10_SECTION_FIRST_RETRIEVAL_PLAN.md](./PHASE10_SECTION_FIRST_RETRIEVAL_PLAN.md)  
**Scope:** Part 4 of Phase 10 — **review half** of the pipeline (10A = retrieval half)  
**Status:** v1 shipped behind `REVIEW_PIPELINE_MODE=section_first`; **v1.1 tasks below** complete production hardening  
**Principle:** Minimal diff. Fix **anchor** (contract-section-first compare) and **judgment quality** without changing storage, ingest, or legacy graph.

---

## 0. Why this plan exists (Plan 1 vs Plan 2)

Phase 10 is intentionally split into two plans that share one graph mode:

| Plan | ID | Fixes | Unit of work |
|------|-----|-------|--------------|
| **Plan 1** | 10A | **Recall** — find the right policies | Per contract section → retrieval bundle |
| **Plan 2** | 10B | **Anchor + judgment** — compare the right texts | Per contract section (batch 2) → LLM → findings |

**10B cannot succeed without 10A.** If retrieval returns nothing, compare LLM has nothing to judge.  
**10A alone is insufficient.** High recall with policy-first compare still compares wrong contract clauses (RC-1).

```text
10A output:  section_retrieval_by_id[section_id] → policy_hits[]
10B input:   contract section FULL text + policy_hits[] → ComplianceFinding[]
```

---

## 1. Root cause analysis (10B-specific)

### 1.1 Symptoms users see (review-side)

| Symptom | User impact |
|---------|-------------|
| Policy exists but finding says “compliant” on wrong contract clause | False negative / missed risk |
| Compare uses policy summary or wrong contract snippet | Unreliable rationale |
| Long indemnity section cut mid-sentence | Wrong or empty finding |
| 40-section contract → 40 separate full-context LLM calls | Cost + latency unacceptable |
| Section A compliant, Section B contradicts A — no reconciliation | Conflicting report |
| Sections with no retrieved policy silently skipped | False “all clear” |
| Inline pasted policies worse than indexed policies | Parser + no stable section IDs |

### 1.2 Root causes mapped to code (verified)

| ID | Root cause | Where (today) | Effect on review | 10B solution |
|----|------------|---------------|------------------|--------------|
| **RC-1** | **Policy-first loop** — categories built from policy sections, then contract searched | `policy_plan.py`, `policy_retrieval.py`, `compliance_review_node` | LLM compares policy text to **wrong or empty** contract snippet | **Skip** `policy_plan` + `policy_retrieval` in `section_first` mode; anchor on `contract_sections[]` from `clause_detection` |
| **RC-9** | **LLM truncation** — `compliance_max_section_chars=12_000` | `compliance_llm._truncate_section`, reused in `section_compare_llm.py` | Long clauses lose tail (often liability caps, carve-outs) | Pass **full** `section.text` by default; truncate only when single section exceeds hard cap **with warning** in state; use `SECTION_COMPARE_MAX_TOKENS` for **batch** budget not per-field chop |
| **RC-10** | **Whole-dimension batching** — legacy hybrid batches by category, not section | `compliance_batch_llm.py` | Cross-clause contamination in one prompt | Batch **2 contract sections** + their retrieved policies only |
| **RC-11** | **No section_id on findings** — dimension_id only | legacy `ComplianceFinding` | Report cannot link finding → contract section | Require `section_id`, `policy_document_id`, `policy_section_id` on every compare item |
| **RC-12** | **Silent skip when retrieval empty** | `section_compare_llm_node` L42–43 | Sections with 0 hits never reach LLM; user may think reviewed | Merge emits `INSUFFICIENT_POLICY_CONTEXT` per section; final gap pass may re-retrieve |
| **RC-13** | **No merge / dedupe across batches** | N/A in legacy | Duplicate findings for same section+policy | Dedupe key `(section_id, policy_document_id, dimension_label)` |
| **RC-14** | **No final reconciliation pass** | `final_gap_verify_node` v1 pass-through | UNCLEAR, conflicts, NO_POLICY not escalated | `final_verify_llm.py` — second pass on gap list only |
| **RC-15** | **Quote verify after compare** — LLM invents quotes | all LLM compare paths | Ungrounded findings in report | Reuse `_validate_and_normalize_quotes` + existing `grounding_node` (unchanged) |
| **RC-8** | **Heuristic parser** on inline policy text | `text_parser.py` | Bad section boundaries → bad LLM input | **Not fixed in 10B** — mitigated by indexing policies via Java/catalog; warn in report when `structure_confidence` low |

### 1.3 Root causes 10B explicitly does NOT fix (handled elsewhere)

| ID | Reason |
|----|--------|
| RC-2, RC-5, RC-6, RC-7 | Fixed in **10A** (recall_top_k, taxonomy, union, reranker) |
| RC-3, RC-4 | Legacy discovery/plan caps — **bypassed** in section_first graph (no `policy_plan`) |
| RC-8 (full fix) | Phase 8 parser / Java extract quality |

---

## 2. Target behavior (production-grade)

### 2.1 Pipeline (section_first mode)

```text
clause_detection
  → index_policies                    # unchanged
  → section_policy_retrieval          # 10A — produces bundles
  → section_compare_llm               # 10B.2 — batch 2 sections
  → merge_section_findings            # 10B.3 — dedupe + NO_POLICY gaps
  → final_gap_verify                  # 10B.4 — gaps + UNCLEAR only
  → grounding                         # unchanged — verify_quote
  → report                            # 10B.5 — extended stats
  → save_memory                       # unchanged
```

### 2.2 Design rules (from earlier discussion)

1. **Section is the unit** — never whole contract in one compare prompt.  
2. **No summarization** — send `IndexedChunk.text` as stored; model reads full section.  
3. **Batch 2 sections per LLM call** — ~half the calls vs batch 1; stay under `SECTION_COMPARE_MAX_TOKENS` (~48k conservative budget; 256k model context available but cost/latency capped).  
4. **No deterministic rule engine** for legal judgment — LLM decides COMPLIANT / NON_COMPLIANT; **quote verification only** is deterministic.  
5. **Legacy unchanged** — `REVIEW_PIPELINE_MODE=legacy` default until QA sign-off.  
6. **Storage unchanged** — `ingest_document` → pgvector → `list_sections`.

### 2.3 LLM call budget (40-section contract example)

| Stage | Calls | Notes |
|-------|-------|-------|
| Section classify (10A) | 0 (lexical) or ~20 (LLM batched) | Optional; lexical default |
| Section compare (10B) | ~20 | batch_size=2, 40 sections with policy |
| Final gap verify (10B) | 0–3 | Only gap + UNCLEAR sections |
| **Total review LLM** | **~20–23** | vs legacy ~30 category compares + plan LLM |

---

## 3. Current implementation status (v1 baseline)

| Task area | Status | Gap |
|-----------|--------|-----|
| 10B.1 Config + graph branch | ✅ Done | — |
| 10B.2 Compare LLM + batching | ✅ Done | Concurrency config unused; truncation still uses 12k cap |
| 10B.3 Merge + NO_POLICY gaps | ✅ Done | UNCLEAR / low-confidence bucket missing |
| 10B.4 Final gap verify | ⚠️ Stub | Pass-through node only; no `final_verify_llm.py` |
| 10B.5 Report stats | ⚠️ Partial | Missing aggregated retrieval path stats |
| 10B.6 Tests | ⚠️ Partial | E2E + merge + classifier; no `test_section_compare.py` |

**Files already in repo (do not rewrite — extend):**

- `review_agent/graph/section_compare_nodes.py`
- `review_agent/services/section_compare_llm.py`
- `review_agent/services/section_merge.py`
- `review_agent/services/token_budget.py`
- `review_agent/schemas/section_compare.py`
- `review_agent/prompts/section_compare.md`
- `review_agent/prompts/final_gap_verify.md` (prompt exists; service missing)

---

## 4. Implementation tasks (detailed)

### 10B.1 — Config & graph mode switch

**Root cause addressed:** RC-1 (legacy graph still default; safe rollout)

| Subtask | Detail | File | Change | Est. |
|---------|--------|------|--------|------|
| 10B.1.1 | Env flag `REVIEW_PIPELINE_MODE=legacy\|section_first` | `review_agent/config.py` | ✅ Exists | 0 |
| 10B.1.2 | Batch + token settings | same | ✅ `section_compare_batch_size=2`, `section_compare_max_tokens=48000` | 0 |
| 10B.1.3 | Concurrency caps | same | ✅ `section_compare_concurrency=3` — **wire in 10B.2.5** | 15 |
| 10B.1.4 | Graph edges — skip legacy nodes | `review_graph.py` | ✅ section_first branch | 0 |
| 10B.1.5 | tenant_auto + section_first | `review_graph.py` | ✅ routing → discovery → index before retrieval | 0 |
| 10B.1.6 | Document `.env.example` | `review_agent/.env.example` | ✅ Done | 0 |
| 10B.1.7 | **Prod template** | `.env.production.example` | Add section_first block with recommended values | 10 |

**Acceptance:** With `REVIEW_PIPELINE_MODE=legacy`, graph identical to pre-Phase-10; all legacy tests pass.

---

### 10B.2 — Section compare LLM (batched)

**Root causes addressed:** RC-1, RC-9, RC-10, RC-11, RC-15

#### 10B.2.1 — Schema (done; minor extensions)

| Subtask | Detail | File | Change | Est. |
|---------|--------|------|--------|------|
| 10B.2.1.a | `SectionCompareItem` fields | `schemas/section_compare.py` | ✅ | 0 |
| 10B.2.1.b | Add optional `truncated: bool` on item metadata | same | Set when section text was hard-capped | 15 |
| 10B.2.1.c | `FinalGapVerifyResult` schema | `schemas/section_compare.py` (new types) | Items for gap pass output | 40 |

#### 10B.2.2 — Prompt hardening

| Subtask | Detail | File | Change | Est. |
|---------|--------|------|--------|------|
| 10B.2.2.a | Base prompt | `prompts/section_compare.md` | ✅ | 0 |
| 10B.2.2.b | Add explicit: “Do not summarize contract or policy text” | same | 5 lines | 5 |
| 10B.2.2.c | Add: “One item per (section, policy_section) pair with material difference” | same | Reduce duplicate items | 5 |
| 10B.2.2.d | Pass `memory_context` when non-empty (tenant precedents) | prompt + service | `{memory_context}` block in USER | 25 |

#### 10B.2.3 — Token budget (done; tune)

| Subtask | Detail | File | Change | Est. |
|---------|--------|------|--------|------|
| 10B.2.3.a | `estimate_tokens` chars/4 | `token_budget.py` | ✅ | 0 |
| 10B.2.3.b | Split batch when > `section_compare_max_tokens` | same | ✅ | 0 |
| 10B.2.3.c | **Separate** single-section hard cap env | `config.py` | `section_compare_max_section_chars` default 32_000 (not 12k compliance cap) | 20 |
| 10B.2.3.d | Emit warning when hard cap applied | `section_compare_llm.py` | Append to return meta / state warnings | 15 |

**Solution detail (RC-9):**

```python
# Wrong (today): reuse compliance_max_section_chars=12000 for every section in batch
# Right: 
#   - Batch budget: SECTION_COMPARE_MAX_TOKENS (48000) controls how many sections fit
#   - Single-section safety: SECTION_COMPARE_MAX_SECTION_CHARS (32000) only if one section alone exceeds budget
#   - Log warning: "section {id} truncated at {n} chars for LLM input"
```

#### 10B.2.4 — Compare service

| Subtask | Detail | File | Change | Est. |
|---------|--------|------|--------|------|
| 10B.2.4.a | `compare_section_batch` | `section_compare_llm.py` | ✅ | 0 |
| 10B.2.4.b | `compare_all_sections` sequential batches | same | ✅ | 0 |
| 10B.2.4.c | **Parallel batches** with `gather_limited(..., limit=section_compare_concurrency)` | same | Wrap batch loop | 30 |
| 10B.2.4.d | Inject `policy_document_id` from hit if LLM leaves blank | same | Post-process: map `policy_section_id` → hit parent | 25 |
| 10B.2.4.e | Pass `memory_context` from state into user prompt | same + node | Thread from `ReviewState` | 20 |
| 10B.2.4.f | On LLM failure: section-level `INSUFFICIENT_POLICY_CONTEXT` not silent `[]` | same | One finding per failed batch section | 30 |

#### 10B.2.5 — Compare node

| Subtask | Detail | File | Change | Est. |
|---------|--------|------|--------|------|
| 10B.2.5.a | Load sections + bundles from state | `section_compare_nodes.py` | ✅ | 0 |
| 10B.2.5.b | Skip sections with 0 policy hits (merge handles gap) | same | ✅ intentional | 0 |
| 10B.2.5.c | Pass `memory_context` from state | same | Add param to `compare_all_sections` | 15 |
| 10B.2.5.d | Enrich `compliance_stats`: `llm_batches_actual`, `sections_truncated` | same | Counters from service | 20 |

#### 10B.2.6 — Tests

| Subtask | Detail | File | Est. |
|---------|--------|------|------|
| 10B.2.6.a | Mock LLM — 2 sections one call | `tests/test_section_compare.py` | 80 |
| 10B.2.6.b | Token budget splits oversized batch | same | 40 |
| 10B.2.6.c | Quote normalization invoked for NON_COMPLIANT | same | 40 |
| 10B.2.6.d | LLM failure → insufficient finding not empty | same | 30 |

**Acceptance:** 40 sections, batch 2 → ≤20 LLM calls; every finding has `contract_section_id`; quotes grounded downstream.

---

### 10B.3 — Merge section findings

**Root causes addressed:** RC-12, RC-13

| Subtask | Detail | File | Change | Est. |
|---------|--------|------|--------|------|
| 10B.3.1 | Dedupe `(section_id, policy_document_id, dimension_label)` | `section_merge.py` | ✅ | 0 |
| 10B.3.2 | NO_POLICY gap for sections with 0 hits **and** no compare items | same | ✅ fixed (skip if compared) | 0 |
| 10B.3.3 | **UNCLEAR bucket** — status INCONCLUSIVE or confidence < 0.5 | same | Collect `gap_sections_unclear[]` in state | 40 |
| 10B.3.4 | **Conflict detection** — same dimension, different status across sections | same | Emit `metadata.conflict_group` | 50 |
| 10B.3.5 | Map to `ComplianceFinding` | same | ✅ `section_items_to_findings` | 0 |
| 10B.3.6 | Node returns `gap_section_ids`, `unclear_finding_ids` | `section_compare_nodes.py` | For 10B.4 input | 25 |
| 10B.3.7 | Tests: dedupe, no double gap, unclear list | `tests/test_section_merge.py` | Extend | 40 |

**Solution detail (RC-13 dedupe):**

```text
Key: (section_id, str(policy_document_id), dimension_label.lower())
Keep: highest severity; prefer NON_COMPLIANT over COMPLIANT on tie
```

**Acceptance:** No duplicate findings in report; every no-hit section has exactly one gap finding.

---

### 10B.4 — Final gap / verify pass

**Root causes addressed:** RC-12, RC-14

**Problem:** First-pass retrieval + compare misses policies for ambiguous sections; LLM returns INCONCLUSIVE; cross-section conflicts need human-readable resolution.

#### 10B.4.1 — Service `final_verify_llm.py` (NEW)

| Subtask | Detail | Change | Est. |
|---------|--------|--------|------|
| 10B.4.1.a | Input builder | Collect: `gap_section_ids`, unclear findings, conflict pairs | 40 |
| 10B.4.1.b | Optional **broad re-retrieve** | For each gap section: call `multi_retrieve_for_section` with `retrieval_recall_top_k=30` | 50 |
| 10B.4.1.c | If re-retrieve hits → run **single-section** compare (no batch) | Reuse `compare_section_batch([section], ...)` | 30 |
| 10B.4.1.d | If still no hits → LLM gap prompt only (status INSUFFICIENT_POLICY_CONTEXT) | `prompts/final_gap_verify.md` | 40 |
| 10B.4.1.e | Conflict resolution prompt slice | Include both section texts + both findings | 40 |
| 10B.4.1.f | Output `FinalGapVerifyResult` → append/replace findings | Merge into state.findings | 30 |

**Solution detail (minimal change):**

```text
Only sections in gap/unclear/conflict sets enter this node.
Typical contract: 40 sections → 2–5 gap sections → 1–2 extra LLM calls (not 40).
Re-retrieve uses existing 10A multi_retrieval — no new search code.
```

#### 10B.4.2 — Node wiring

| Subtask | File | Change | Est. |
|---------|------|--------|------|
| 10B.4.2.a | Replace pass-through | `section_compare_nodes.py` | Call `final_verify_llm.run(state, client)` | 40 |
| 10B.4.2.b | State fields | `review_state.py` | `gap_section_ids`, `final_verify_stats` | 15 |
| 10B.4.2.c | Config gate | `config.py` | `final_gap_verify_enabled: bool = True` | 10 |

#### 10B.4.3 — Tests

| Subtask | File | Est. |
|---------|------|------|
| Gap section re-retrieve finds policy → new NON_COMPLIANT | `tests/test_final_gap_verify.py` | 90 |
| Still no policy → INSUFFICIENT retained | same | 40 |
| Mock re-retrieve only (no postgres) | same | 30 |

**Acceptance:** Gap sections never absent from final report; re-retrieve proven in test with seeded corpus.

---

### 10B.5 — Grounding & report (extend only)

**Root causes addressed:** RC-15 (downstream)

| Subtask | Detail | File | Change | Est. |
|---------|--------|------|--------|------|
| 10B.5.1 | Grounding node | `nodes.py` | **No change** — uses `findings[]` + `verify_quote` | 0 |
| 10B.5.2 | Report metadata stats | `nodes.report_node` | Add aggregates from `section_retrieval_by_id`: | 40 |
| | | | `sections_reviewed`, `sections_no_policy`, `sections_with_policy` | |
| | | | `llm_batches_actual`, `retrieval_paths_used` (dense/fts/meta counts) | |
| | | | `review_pipeline_mode`, `final_gap_verify_ran` | |
| 10B.5.3 | Markdown report section | `reports/generator.py` | Optional “Section-first summary” block | 30 |
| 10B.5.4 | Warnings propagation | merge + compare nodes | Parser low confidence, truncation, gap count | 20 |

**Acceptance:** Report JSON metadata sufficient for ops dashboard; markdown mentions section-first stats when mode active.

---

### 10B.6 — E2E & regression

| Subtask | Detail | File | Est. |
|---------|--------|------|------|
| 10B.6.1 | Section-first E2E (mock LLM) | `tests/test_review_e2e_section_first.py` | ✅ extend golden asserts | 30 |
| 10B.6.2 | Legacy regression | CI / local pytest | All tests with `REVIEW_PIPELINE_MODE=legacy` | 0 |
| 10B.6.3 | Golden fixture: NDA + 2 policies | `tests/fixtures.py` | Expect ≥1 NON_COMPLIANT with real LLM (optional nightly) | 80 |
| 10B.6.4 | Postgres integration | existing conftest | Full graph with pgvector | 0 |

**Acceptance criteria (10B done):**

- [ ] Full section text in compare prompt (no summary field in pipeline)  
- [ ] Batch 2 → ~50% LLM calls vs batch 1  
- [ ] Findings include `contract_section_id` + grounded quotes  
- [ ] NO_POLICY sections in report warnings + gap findings  
- [ ] Final gap pass runs on gap/unclear only  
- [ ] Legacy mode: 52+ tests pass unchanged  

---

## 5. Sprint breakdown (10B v1.1 — recommended order)

### Sprint B1 — Hardening compare (low risk)

1. 10B.2.3.c/d — separate section char cap + warnings  
2. 10B.2.4.c — batch concurrency  
3. 10B.2.4.d/e — policy_document_id backfill + memory_context  
4. 10B.2.6 — unit tests  

**Demo:** Same contract, compare 2 sections in parallel batches; truncation warning in report.

### Sprint B2 — Merge quality

1. 10B.3.3 — UNCLEAR bucket  
2. 10B.3.4 — conflict detection  
3. 10B.3.7 — merge tests  

**Demo:** Inject conflicting mock items → report flags conflict metadata.

### Sprint B3 — Final gap verify (production closure)

1. 10B.4.1 — `final_verify_llm.py`  
2. 10B.4.2 — node wiring  
3. 10B.4.3 — tests  
4. 10B.5.2 — report stats aggregation  

**Demo:** Section with 0 first-pass hits → re-retrieve → finding or explicit INSUFFICIENT.

### Sprint B4 — QA & prod config

1. 10B.6.3 golden fixture  
2. 10B.1.7 production env template  
3. Sign-off: flip `REVIEW_PIPELINE_MODE=section_first` in staging  

---

## 6. Config reference (10B)

```env
# Mode
REVIEW_PIPELINE_MODE=section_first

# Compare batching
SECTION_COMPARE_BATCH_SIZE=2
SECTION_COMPARE_MAX_TOKENS=48000
SECTION_COMPARE_MAX_SECTION_CHARS=32000   # NEW v1.1 — single-section hard cap
SECTION_COMPARE_CONCURRENCY=3

# Final pass
FINAL_GAP_VERIFY_ENABLED=true
FINAL_GAP_RECALL_TOP_K=30

# Inherited (reuse — do not duplicate)
COMPLIANCE_LLM_TEMPERATURE=0
COMPLIANCE_LLM_MAX_TOKENS=2048
REVIEW_MIN_SECTION_CHARS=40               # sections shorter skipped entirely
```

---

## 7. Files touched (minimal diff summary)

### New (v1.1)

| Path | Purpose |
|------|---------|
| `review_agent/services/final_verify_llm.py` | Gap + unclear + conflict pass |
| `review_agent/tests/test_section_compare.py` | Compare unit tests |
| `review_agent/tests/test_final_gap_verify.py` | Gap pass tests |

### Modify only

| Path | Change |
|------|--------|
| `review_agent/services/section_compare_llm.py` | Concurrency, caps, memory, failure handling |
| `review_agent/services/section_merge.py` | UNCLEAR + conflicts |
| `review_agent/graph/section_compare_nodes.py` | Wire final verify + stats |
| `review_agent/graph/nodes.py` | Report metadata aggregation |
| `review_agent/config.py` | 3 new env vars |
| `review_agent/state/review_state.py` | gap/unclear state keys |
| `review_agent/prompts/section_compare.md` | Anti-summary + memory block |
| `review_agent/schemas/section_compare.py` | FinalGap types |

### Do NOT modify

| Path | Reason |
|------|--------|
| `document_core/store/*` | 10A scope |
| `policy_plan.py`, `policy_retrieval.py` | Legacy only |
| `grounding.py` | Already correct |
| `deep_research/*` | Out of scope |

---

## 8. Risks & mitigations (10B-specific)

| Risk | Mitigation |
|------|------------|
| LLM cost scales with section count | Batch 2; skip `review_min_section_chars`; gap pass only on failures |
| 48k batch budget too small for 2 long sections | Auto split to batch 1 via `token_budget.py` (already implemented) |
| LLM omits `policy_document_id` | Backfill from retrieval hit map in post-process |
| False gap findings when compare ran | Merge skips gap if section in compare items (fixed) |
| Cross-section liability conflict missed | 10B.3.4 conflict metadata + 10B.4 conflict prompt |
| Prod rollout breaks tenants | `legacy` default; feature flag per tenant later (platform concern) |

---

## 9. Comparison to industry pattern

| Product pattern | Phase 10B equivalent |
|-----------------|----------------------|
| ContractPodAi / Kira — clause segmentation first | `clause_detection` → per-section loop |
| NLP retrieval before LLM judgment | 10A multi_retrieve → 10B compare |
| Playbook chunk compare | Policy parent text in compare prompt |
| Human-in-loop on low confidence | UNCLEAR → final gap pass + report severity INFO |

---

## 10. Checklist (copy for tracking)

```
10B.1 Config & graph
  [x] REVIEW_PIPELINE_MODE
  [x] Graph branch
  [ ] .env.production.example

10B.2 Section compare LLM
  [x] Schema + prompt + service + node
  [ ] section_compare_max_section_chars (separate from 12k)
  [ ] section_compare_concurrency wired
  [ ] memory_context in prompt
  [ ] LLM failure → insufficient finding
  [ ] test_section_compare.py

10B.3 Merge
  [x] Dedupe + NO_POLICY gaps
  [ ] UNCLEAR bucket
  [ ] Conflict detection

10B.4 Final gap verify
  [ ] final_verify_llm.py
  [ ] Re-retrieve gap sections
  [ ] Node wired (replace stub)
  [ ] test_final_gap_verify.py

10B.5 Report
  [x] Basic compliance_stats
  [ ] Full retrieval path aggregation
  [ ] Generator section-first block

10B.6 E2E
  [x] test_review_e2e_section_first (mock)
  [ ] Golden NDA fixture
  [ ] Legacy regression sign-off
```

---

**Total v1.1 estimate:** ~900 lines + tests (on top of ~1,500 lines v1 already shipped).  
**Critical path:** 10B.2 hardening → 10B.4 final gap verify → 10B.6 golden E2E.

*Plan 2 fixes judgment anchor; Plan 1 (10A) fixes recall. Both required for production accuracy.*
