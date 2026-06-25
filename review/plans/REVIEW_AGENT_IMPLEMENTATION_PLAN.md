# Review Agent — Production Implementation Plan

**Version:** 1.0  
**Scope:** Phase A (baseline) → Phase B (retrieval safety) → Phase C (compare intelligence)  
**Principle:** Fix root causes with minimal diffs; measure after each phase.

---

## 1. Problem statement

Xecurify NDA review scored **~6.5/10 production readiness**. Root failure mode:

```text
Contract section classified correctly
    → retrieval returns WRONG policy document/chunk (shared broad tags: compliance, security)
    → compare LLM judges against wrong evidence
    → false NON_COMPLIANT + noisy INCONCLUSIVE
```

Policy discovery works (5/5). **Retrieval + compare inputs** are the bottleneck — not the graph architecture.

---

## 2. Pipeline (where each fix lands)

```text
contract_parser
    → contract_routing
    → policy_discovery
    → section_policy_retrieval     ← Phase B (routing, relevance, coverage)
    → section_compare_llm          ← Phase C (incorporation, bundling)
    → merge_section_findings
    → final_gap_verify
    → grounding
    → report
```

**Ingest path (separate):**

```text
sync / index_policy
    → text_parser                  ← Phase A3, A9
    → category_tagger (LLM)        ← Phase A1
    → save_document (pgvector)     ← Phase A5, A9
```

---

## 3. Phase A — Baseline & unblock

**Goal:** Stable ingest + LLM tags + clean baseline metrics.  
**Estimated code:** ~120 LOC (mostly done). **Operator:** A6–A8.

### A1 — Enable LLM policy tagger ✅ CODE | 🔄 OPS

| Field | Detail |
|-------|--------|
| **Symptom** | All policies tagged `"tagger": "keyword"`; broad substring tags (`sla` from "slavery", `security` from "brand security"). |
| **Root cause** | `document_core/.env` had `CATEGORY_TAGGER_MODE=auto` but **no `LLM_API_KEY`**. `llm_api_key_available()` → false → keyword fallback in `category_tagger.py:98–109`. |
| **Fix** | Set `LLM_API_KEY`, `LLM_BASE_URL`, `CATEGORY_TAGGER_MODE=llm` in `document_core/.env`. |
| **Files** | `document_core/.env`, `document_core/.env.example` |
| **Production note** | MCP loads `document_core/.env` at start only — not `temp_java_sync/.env`. |
| **Verify** | `sync_result.json` → `"tagger": "llm"` on every policy. |

### A2 — Restart document-mcp ✅ OPS

| Field | Detail |
|-------|--------|
| **Root cause** | Env and Python code loaded at process start; crashes leave stale listener on :8003. |
| **Fix** | `.\scripts\start_document_mcp.ps1 -Replace` after any `document_core` env or code change. |
| **Verify** | `GET /health` → `status: ok`, `db: ok`. |

### A3 — Parser duplicate section IDs ✅ CODE

| Field | Detail |
|-------|--------|
| **Symptom** | `POST /tools/index_policy` → 500 `UniqueViolation` on `chunk_id …:3`. |
| **Root cause** | `c. Low Severity (Level 3):` matched Roman heading `[IVXLC]+` (case-insensitive `c.`). `_derive_section_id` extracted `3` from `(Level 3)`, colliding with `3. Roles`. |
| **Fix (minimal)** | ① `[IVXLC]{2,}` for Roman headings. ② `_derive_section_id` prefers line-start numbers. ③ `_dedupe_section_ids()` before chunking. |
| **Files** | `document_core/parser/text_parser.py`, `tests/test_text_parser.py` |
| **Verify** | `pytest tests/test_text_parser.py`; Incident Response policy syncs without 500. |

### A4 — Retrieval crash on routing topics ✅ CODE

| Field | Detail |
|-------|--------|
| **Symptom** | `retrieval failed for section 1: 'str' object has no attribute 'get'` (8 sections). |
| **Root cause** | `contract_routing.topics` is `list[str]`; `_query_for_attempt` called `.get()` on each item. |
| **Fix** | Branch: `isinstance(topic, str)` vs `dict`. |
| **Files** | `review_agent/services/multi_retrieval.py` (~8 LOC) |
| **Verify** | Re-run review; zero `retrieval failed … get` warnings. |

### A5 — Tombstone orphan chunks ✅ CODE

| Field | Detail |
|-------|--------|
| **Symptom** | Re-sync finds old chunks; search returns deleted policy content. |
| **Root cause** | `tombstone_policy_by_ref` set `index_status=deleted` but did not delete `document_chunks` / `document_canonical`. |
| **Fix** | `DELETE FROM document_chunks` + `document_canonical` in same transaction as tombstone. |
| **Files** | `document_core/store/pgvector_store.py` |
| **Verify** | Tombstone policy → chunk count 0 for that `document_id`. |

### A6 — Re-index all policies ⏳ OPS

| Field | Detail |
|-------|--------|
| **Root cause** | DB rows still have keyword-era `metadata.categories` on parent chunks. |
| **Action** | Dev UI **Index policies** OR `python test_xecurify_policies.py` (stops after sync if you Ctrl+C before review). |
| **Prereq** | A1, A2, MCP healthy. |
| **Verify** | `sync_result.json`: all `index_status_after: indexed`, no 500s. |

### A7 — Verify LLM tagger ran ⏳ OPS

| Field | Detail |
|-------|--------|
| **Action** | Open `outputs/sync_result.json` → every policy `"tagger": "llm"`. |
| **If keyword** | MCP logs: `category tagger LLM failed` (429, bad key). Confirm key in env MCP actually loads. |

### A8 — Baseline assessment ⏳ OPS

| Field | Detail |
|-------|--------|
| **Action** | Run full review (Dev UI or `test_xecurify_policies.py`). Export → `xecurify_nda_assessment.json`. |
| **Record** | `weighted_alignment_score`, violation count, `retrieval failed` count, INCONCLUSIVE %. |
| **Success** | No retrieval crashes; score recorded for Phase B A/B comparison. |

### A9 — Contract re-ingest UniqueViolation ✅ CODE

| Field | Detail |
|-------|--------|
| **Symptom** | Contract ingest 500 on `chunk_id …:1` with title `1. Is or becomes publicly available…`. |
| **Root cause 1** | Numbered **exclusion prose** parsed as section heading (`section_id=1` collides with `1. Definitions`). |
| **Root cause 2** | `save_document` hash check outside write transaction → race on concurrent re-ingest. |
| **Fix (minimal)** | ① `_is_prose_list_line()` — skip `;`-terminated / sentence-style numbered lines in `_match_heading`. ② Single `begin()` transaction: check → delete → insert. ③ `ON CONFLICT (tenant_id, document_id, chunk_id) DO UPDATE` on chunks. |
| **Files** | `text_parser.py`, `pgvector_store.py`, tests |
| **Verify** | `pytest tests/test_text_parser.py tests/test_pgvector_save_document.py`; ingest same contract twice → 200. |

### Phase A — execution order

```text
A1 ✅ → A2 ✅ → A3–A5 ✅ → A9 ✅ → [restart MCP] → A6 → A7 → A8
```

### Phase A — done when

- [ ] `sync_result.json`: all policies `tagger: llm`
- [ ] Policy sync: no `UniqueViolation`
- [ ] Review: no `retrieval failed … get`
- [ ] Fresh `xecurify_nda_assessment.json` with baseline metrics

---

## 4. Phase B — Retrieval safety (highest ROI)

**Goal:** Only relevant policy evidence reaches compare LLM.  
**Estimated code:** ~200–280 LOC. **Do after Phase A8 baseline.**

### B1 — Named-policy routing

| Field | Detail |
|-------|--------|
| **Symptom** | Section 2.3 (Security Measures) compared against Data Retention deletion clause. |
| **Root cause** | Contract text names policies (`Xecurify's Security Practices Policy`) but retrieval uses only category + embedding search. |
| **Fix (minimal)** | New helper `extract_named_policy_refs(section_text) → list[str]`. In `multi_retrieve_for_section`, if names found: resolve `policy_ref` via registry → set `filter_doc_ids` to those UUIDs for attempt 0. Fall back to existing path if no hits. |
| **Files** | `review_agent/services/named_policy_routing.py` (new, ~60 LOC), `multi_retrieval.py` (~15 LOC wire) |
| **Config** | `NAMED_POLICY_ROUTING_ENABLED=true` (default on) |
| **Verify** | Section 2.2/2.3 retrieves Security Practices doc_id, not Data Retention. |

### B2 — Chunk relevance gate

| Field | Detail |
|-------|--------|
| **Symptom** | Retrieved hit topic unrelated to section (deletion clause for security section). |
| **Root cause** | Hybrid search ranks by similarity; no post-filter on category/title alignment. |
| **Fix (minimal)** | `filter_hits_by_relevance(hits, section_categories, section_title) → hits` — keep hit if parent `metadata.categories` intersects section categories (excluding `general`) OR title token overlap ≥ threshold. Drop rest before rerank final top-k. |
| **Files** | `review_agent/services/retrieval_relevance.py` (new, ~80 LOC), `multi_retrieval.py` wire after union/rerank |
| **Verify** | Security section hits exclude pure data_retention-only parents. |

### B3 — Policy coverage validator (pre-compare gate)

| Field | Detail |
|-------|--------|
| **Symptom** | Compare runs with Code of Conduct + Data Retention + Incident Response for Human Rights section; invents violations from wrong doc. |
| **Root cause** | No check that retrieved **policy documents** match expected families before compare. |
| **Fix (minimal)** | `validate_policy_coverage(section, hits, discovered_policies) → CoverageResult` with `relevant_hits`, `irrelevant_doc_ids`, `coverage_score`. If `coverage_score < COVERAGE_MIN_THRESHOLD` (default 0.5): emit single `INSUFFICIENT_POLICY_CONTEXT` finding; **do not** call compare for irrelevant hits. If partial: pass only `relevant_hits` to compare. |
| **Wire point** | `section_compare_nodes.py` before `compare_all_sections` (~20 LOC). |
| **Files** | `review_agent/services/policy_coverage.py` (new, ~100 LOC), `config.py` (+2 settings), tests |
| **Verify** | Human Rights section: only Code of Conduct hits reach compare; Data Retention excluded. |

### B4 — Category intersection scoring (discovery + filter)

| Field | Detail |
|-------|--------|
| **Root cause** | Single shared tag `compliance` matches 4+ policies. |
| **Fix (minimal)** | In `_resolve_filter_document_ids` / discovery: score policies by `\|contract_categories ∩ policy_categories\|`; require ≥2 overlap when section has ≥2 non-general categories, else ≥1 specific category. |
| **Files** | `multi_retrieval.py`, `policy_discovery.py` (~40 LOC) |
| **Verify** | `list_policy_ids_by_categories` returns fewer false-positive docs for `human_rights` sections. |

### B5 — Boilerplate fast-path

| Field | Detail |
|-------|--------|
| **Symptom** | Sections 10.2–10.7 retrieval fails or returns random policy. |
| **Root cause** | Substantive retrieval attempted on boilerplate classified as `general` only. |
| **Fix** | If `is_non_substantive_section(section)` and categories are `general`-only: skip retrieval; set bundle `skipped_reason: boilerplate`; compare emits `INSUFFICIENT_POLICY_CONTEXT` with severity `info`. (Partially exists — ensure zero-hit does not log as error.) |
| **Files** | `section_retrieval_nodes.py`, `section_gap_status.py` (~25 LOC) |
| **Verify** | Notices/counterparts → IPC, not NON_COMPLIANT. |

### Phase B — execution order

```text
B1 → B2 → B3 → B4 → B5 → re-run Xecurify (record metrics)
```

### Phase B — success metrics

| Metric | Baseline (pre-B) | Target |
|--------|------------------|--------|
| False positive violations | ~3–4 / 10 | ≤1 |
| Wrong-policy compare pairs | Security→Retention etc. | 0 |
| INCONCLUSIVE | ~27% | <15% |
| Weighted alignment | ~48 | ≥65 |

---

## 5. Phase C — Compare intelligence

**Goal:** Correct judgments when evidence is right. **After Phase B.**

### C1 — Incorporation-by-reference

| **Root cause** | Compare prompt requires verbatim policy language; contract adopting policy by name marked NON_COMPLIANT. |
| **Fix** | Add rule to `section_compare.md`: if contract references org policy by name without contradiction → `COMPLIANT` or `INCONCLUSIVE(info)`, not NON_COMPLIANT. |
| **Files** | `prompts/section_compare.md` only (~15 lines) |
| **Verify** | Code of Conduct acknowledgment → COMPLIANT. |

### C2 — Cross-section bundling

| **Root cause** | Section 2.1 compared alone; deletion in 3.2 missed. |
| **Fix** | Extend compare batch: for retention/deletion/termination categories, append related section excerpts from `section_context_by_id` (already built for classify). |
| **Files** | `section_compare_llm.py`, `section_cross_reference.py` (~50 LOC) |
| **Verify** | No 2.1 NON_COMPLIANT when 3.2 has secure deletion. |

### C3 — Cross-section finding dedupe

| **Root cause** | Same dimension flagged NON_COMPLIANT in one section, COMPLIANT in another. |
| **Fix** | Post-merge pass: group by `dimension_label` + policy family; if any COMPLIANT with grounded quotes, drop weaker NON_COMPLIANT on same topic. |
| **Files** | `finding_dedupe.py` (~40 LOC) |
| **Verify** | Secure deletion appears once, COMPLIANT. |

### C4 — Semantic equivalence guard (optional, low priority)

| **Fix** | Post-compare rule: if contract quote contains "legal retention" and policy quote contains "required by law" on same dimension → downgrade CRITICAL NON_COMPLIANT to COMPLIANT. |
| **Files** | `section_merge.py` or small `equivalence_guard.py` (~60 LOC) |

### Phase C — success metrics

| Metric | Target |
|--------|--------|
| Weighted alignment | ≥75 |
| Production readiness | 8+/10 |
| INCONCLUSIVE | <10% |

---

## 6. Phase D — Taxonomy (defer until B+C plateau)

Expand `STANDARD_POLICY_CATEGORIES` to 40–60 specific tags. Only after measuring B+C — avoids large blast radius before retrieval is fixed.

| Priority | Tags to add |
|----------|-------------|
| High | `secure_deletion`, `legal_hold`, `data_subject_rights`, `incident_reporting`, `forced_labor`, `modern_slavery`, `trademark` |
| Medium | `aml`, `anti_bribery`, `cross_border_transfer`, `breach_notification` |

Also harden keyword fallback: `\bsla\b` word boundary, document-title priors.

---

## 7. File change matrix (minimal surface)

| File | Phase | Change size |
|------|-------|-------------|
| `document_core/.env` | A1 | config |
| `document_core/parser/text_parser.py` | A3, A9 | ~50 LOC |
| `document_core/store/pgvector_store.py` | A5, A9 | ~40 LOC |
| `review_agent/services/multi_retrieval.py` | A4, B1, B2, B4 | ~80 LOC |
| `review_agent/services/named_policy_routing.py` | B1 | new ~60 LOC |
| `review_agent/services/retrieval_relevance.py` | B2 | new ~80 LOC |
| `review_agent/services/policy_coverage.py` | B3 | new ~100 LOC |
| `review_agent/graph/section_compare_nodes.py` | B3 | ~20 LOC |
| `review_agent/prompts/section_compare.md` | C1 | ~15 lines |
| `review_agent/services/finding_dedupe.py` | C3 | ~40 LOC |

**Total new production code (B+C):** ~350–400 LOC across 6–8 files.

---

## 8. Test strategy (production-grade)

| Layer | Tests |
|-------|-------|
| Unit | `test_text_parser.py`, `test_pgvector_save_document.py`, `test_named_policy_routing.py`, `test_policy_coverage.py` |
| Integration | `test_xecurify_policies.py` (sync + review) |
| Regression | `test_acme_nda_e2e.py` |
| Manual | Dev UI sync → review → check `sync_result.json` + assessment JSON |

**Gate:** No phase merge without pytest green + Xecurify metrics recorded.

---

## 9. Rollout checklist

```text
□ Restart document-mcp (-Replace)
□ A6: Re-index policies
□ A7: Confirm tagger=llm
□ A8: Baseline assessment JSON saved
□ Implement B1–B3 (one PR)
□ Re-run Xecurify, compare metrics
□ Implement C1–C3 (one PR)
□ Re-run Xecurify, compare metrics
□ Phase D only if alignment < 75
```

---

## 10. What NOT to do (avoid over-engineering)

- Do **not** expand taxonomy before Phase B (large change, unclear ROI).
- Do **not** add a second LLM call for coverage validation — use deterministic category/title overlap first.
- Do **not** change compare model or graph topology — fix evidence pipeline.
- Do **not** skip A8 baseline — you need before/after numbers.

---

## 11. Current status snapshot

| Phase | Code | Ops |
|-------|------|-----|
| A1–A5, A9 | ✅ Implemented | — |
| B1–B5 | ✅ Implemented | — |
| C1–C6 | ✅ Implemented | — |
| D1–D7 | ✅ Implemented | re-index + measure |
| E1–E4 | ✅ Implemented | re-review Xecurify |
| **F1–F5** | ✅ Implemented | Mistral profile in `.env`; platform script |
| A2, A6–A8 | — | ⏳ Pending (restart MCP, re-sync, re-review) |

**Immediate next step:** Finish A6–A8 baseline ops, then Phase F (429 profile → assessment export → platform script → dual regression smoke).
