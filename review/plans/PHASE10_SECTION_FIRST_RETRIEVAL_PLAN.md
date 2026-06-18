# Phase 10 — Section-First Review + High-Recall Policy Retrieval

**Plan ID:** `DR-PHASE-10`  
**Status:** Implemented (v1 — `final_gap_verify` pass-through; reranker no-op)  
**Principle:** Fix **recall** (retrieval) and **anchor** (contract-section-first review). Keep storage, ingest, grounding, Java sync. Add new graph mode behind env flag — legacy path unchanged.  
**Depends on:** Phase 7 (indexed policies), document-mcp pgvector hybrid search, existing `contract_parser` + `clause_detection`

---

## Part 0 — Root cause (why accuracy fails today)

### Symptom

- Missed policies for a clause  
- Wrong contract text compared to policy  
- Inline pasted policies perform worse  
- “Summary / cap 30 categories” loses coverage  

### Root causes (verified in code)

| # | Root cause | Where | Effect |
|---|------------|-------|--------|
| RC-1 | **Policy-first loop** — review categories built from policy parent sections, then **search** finds contract clause | `policy_plan.py`, `policy_retrieval.py` | Wrong or missing contract text for compare |
| RC-2 | **Low recall retrieval** — `policy_search_top_k=5`, single hybrid query | `config.py`, `search.py` | Relevant policy chunks never retrieved |
| RC-3 | **Discovery cap** — max 8 policies, 3 hits/topic | `policy_discovery.py` | Whole playbooks never enter review |
| RC-4 | **Plan cap** — `review_max_categories=30` | `policy_plan.py` | Large policies truncated |
| RC-5 | **Weak taxonomy** — only `policy_type` + `applies_to_contract_types`; no `categories[]` | `IngestRequest`, `policy_documents.metadata` | Metadata filter cannot narrow/boost policy families |
| RC-6 | **No reranker** — top hybrid score only | `pgvector_store._search_hybrid` | False positives; no second-stage precision |
| RC-7 | **No union retrieval** — one search path per category | `resolve_policy_hits` | Vector misses keyword-only matches (and vice versa) |
| RC-8 | **Heuristic parser** for inline text | `text_parser.py` | Bad sections → bad retrieval + bad LLM input |
| RC-9 | **LLM truncation** — `compliance_max_section_chars=12_000` | `compliance_llm.py` | Long clauses cut mid-thought |

### Fix strategy (two plans, one pipeline)

```text
10A — High-recall retrieval (per contract section)
10B — Section-first LLM review (batch 2 sections, final verify)

Storage unchanged: ingest → pgvector → list_sections
Legacy mode unchanged: REVIEW_PIPELINE_MODE=legacy (default until QA)
New mode: REVIEW_PIPELINE_MODE=section_first
```

**Not the fix:** swapping LegalLlama vs LegalBERT alone, or Qdrant migration (Postgres pgvector + FTS is enough).

---

## Part 1 — Target architecture

```text
                    ┌─────────────────────────────────────┐
                    │  Contract + policies in pgvector     │
                    │  (ingest unchanged)                  │
                    └──────────────────┬──────────────────┘
                                       │
load_memory → contract_parser → clause_detection → index_policies
                                       │
                         ┌─────────────┴─────────────┐
                         │  SECTION-FIRST (new)       │
                         └─────────────┬─────────────┘
                                       │
              For each contract section (batch 2 for LLM):
                                       │
         ┌─────────────────────────────┼─────────────────────────────┐
         ▼                             ▼                             ▼
  policy_category_classify      multi_retrieve_policy          (parallel)
  (section → categories)        dense + FTS + metadata
                                       │
                                 union + dedupe
                                       │
                                   reranker
                                       │
                              top 8–10 policy parents
                                       │
                         section_compare_llm (batch 2)
                                       │
                         merge_section_findings
                                       │
                         final_gap_verify_llm
                                       │
                         grounding (verify_quote) → report
```

---

## Part 2 — What we keep (no change)

| Component | Path | Notes |
|-----------|------|-------|
| Contract ingest | `nodes.contract_parser_node` | `ingest_document` |
| Section list | `nodes.clause_detection_node` | `list_sections` |
| Policy index | `nodes.index_policies_node` | refs, discovered, inline |
| pgvector hybrid | `PgVectorDocumentStore._search_hybrid` | Extend, don’t replace |
| Grounding | `grounding_node` + `document_core/services/grounding.py` | Quote verify |
| Report | `report_node` | Extend stats fields |
| Java catalog / registry | Phase 7 | Add `categories` in metadata |
| Legacy graph | `REVIEW_PIPELINE_MODE=legacy` | All existing tests pass |

---

## Part 3 — Phase 10A: High-recall policy retrieval

**Goal:** For each contract section, maximize **recall** of relevant policy chunks before LLM compare.

### 10A.1 — Policy taxonomy at index time

**Problem:** RC-5 — no policy family tags for metadata search.

| Task | Subtasks | Files | Est. lines |
|------|----------|-------|------------|
| 10A.1.1 Define taxonomy schema | Standard list: `security`, `vendor_security`, `privacy`, `data_retention`, `hr`, `procurement`, `ai_usage`, … | `document_core/schemas/taxonomy.py` (new) | 60 |
| 10A.1.2 Extend ingest | Accept `categories: list[str]` on `IngestRequest`; store in `policy_documents.metadata` + chunk metadata | `chunk.py`, `ingest.py`, `pgvector_store.py` | 40 |
| 10A.1.3 Migration | Optional GIN index on `metadata->'categories'` | `004_policy_categories.sql` | 25 |
| 10A.1.4 Java contract | Document `metadata.categories` in `JAVA_CATALOG_API_CONTRACT.md` | plans doc | 20 |
| 10A.1.5 MCP register_policy | Pass categories through registry + index | `document_server/main.py`, `catalog_sync.py` | 30 |
| 10A.1.6 Tests | Index with categories; metadata search returns doc | `document_core/tests/test_taxonomy.py` | 80 |

**Acceptance:** Policy indexed with `categories: ["vendor_security","security"]` is findable by category filter.

---

### 10A.2 — Section → policy category classifier

**Problem:** RC-1, RC-5 — need predicted policy families per **contract section**, not just contract-level routing topics.

| Task | Subtasks | Files | Est. lines |
|------|----------|-------|------------|
| 10A.2.1 Schema | `SectionCategoryResult`: `section_id`, `categories[]`, `query_terms[]` | `review_agent/schemas/section_classify.py` | 35 |
| 10A.2.2 Prompt | `prompts/section_policy_classify.md` — full section text in, categories out (no summary) | prompts | 40 |
| 10A.2.3 Service | `classify_section_policies(section, contract_type)` — LLM structured or lightweight model | `services/section_classifier.py` | 120 |
| 10A.2.4 Config | `section_classify_mode: llm \| lexical`, `section_classify_max_chars` | `review_agent/config.py` | 15 |
| 10A.2.5 Lexical fallback | Map section title/keywords → categories via YAML hints (fail-open) | `data/policy_category_hints.yaml` | 50 |
| 10A.2.6 Tests | Mock LLM; NDA indemnity section → liability/vendor categories | `tests/test_section_classifier.py` | 90 |

**Acceptance:** Given section “Vendor shall implement adequate security controls”, classifier outputs includes `security` and/or `vendor_security`.

**Note:** v1 uses **review LLM** (structured JSON) per section or batched 2 sections — same batching as compare. Optional v1.1: fine-tuned encoder.

---

### 10A.3 — Multi-path retrieval (dense + FTS + metadata)

**Problem:** RC-2, RC-7 — single `top_k=5` hybrid search misses policies.

| Task | Subtasks | Files | Est. lines |
|------|----------|-------|------------|
| 10A.3.1 Config | `retrieval_recall_top_k=20`, `retrieval_final_top_k=10`, paths enable flags | `document_core/config.py`, `review_agent/config.py` | 25 |
| 10A.3.2 Dense path | Wrap existing `search_policy` with `top_k=recall_top_k` | reuse | 0 |
| 10A.3.3 FTS path | Add `search_policy_fts_only` — pure `ts_rank`, no vector | `pgvector_store.py`, `search.py` | 80 |
| 10A.3.4 Metadata path | `list_policies_by_categories(tenant, categories[], contract_type)` | `pgvector_store.py`, `search.py` | 100 |
| 10A.3.5 Union service | `multi_retrieve_policies(section, categories, queries) → list[ScoredHit]` dedupe by `parent_chunk_id` | `review_agent/services/multi_retrieval.py` | 150 |
| 10A.3.6 Parallel fetch | `gather_limited` — 3 paths concurrent per section | reuse `async_limits.py` | 20 |
| 10A.3.7 MCP tools | Optional `search_policy_multi` tool for debugging | `document_server/main.py` | 40 |
| 10A.3.8 Tests | Seed 3 policies; keyword-only match found via FTS path | `tests/test_multi_retrieval.py` | 120 |

**Acceptance:** Union of 3 paths returns chunk missed by dense-only search (test with controlled corpus).

---

### 10A.4 — Reranker

**Problem:** RC-6 — 60 union candidates too noisy for LLM.

| Task | Subtasks | Files | Est. lines |
|------|----------|-------|------------|
| 10A.4.1 Interface | `Reranker.score(query, passages[]) → float[]` protocol | `document_core/search/reranker.py` | 30 |
| 10A.4.2 No-op default | Pass-through sort by retrieval score | same file | 25 |
| 10A.4.3 Cross-encoder optional | `BAAI/bge-reranker-v2-m3` or API wrapper; env `RERANKER_ENABLED` | `embedding/reranker_service.py` | 120 |
| 10A.4.4 Wire | After union, rerank → `retrieval_final_top_k` parent sections | `multi_retrieval.py` | 30 |
| 10A.4.5 Tests | Order changes when reranker enabled (mock scores) | `tests/test_reranker.py` | 60 |

**Acceptance:** Union 40 → rerank → top 10; LLM receives ≤10 policy parents per section.

**v1 ship:** No-op reranker + score sort OK; enable cross-encoder in 10A.4.3 when GPU/API ready.

---

### 10A.5 — Section retrieval orchestration node

| Task | Subtasks | Files | Est. lines |
|------|----------|-------|------------|
| 10A.5.1 State fields | `section_retrieval_by_id: dict[str, SectionRetrievalBundle]` | `review_state.py` | 25 |
| 10A.5.2 Filter sections | Skip `len(text) < review_min_section_chars`; split mega-sections optional | `services/section_filter.py` | 60 |
| 10A.5.3 Node | `section_policy_retrieval_node` — loop sections, classify, multi-retrieve, rerank | `graph/section_retrieval_nodes.py` | 140 |
| 10A.5.4 Cache | Same policy doc retrieved for adjacent sections — cache by `(tenant, doc_id)` in node | inline | 20 |
| 10A.5.5 Tests | 3 sections → 3 bundles in state | `tests/test_section_retrieval.py` | 100 |

---

### 10A summary checklist

```
[x] 10A.1 taxonomy schema + ingest + Java metadata.categories
[x] 10A.2 section → category classifier (+ lexical fallback)
[x] 10A.3 dense + FTS + metadata union (recall_top_k=20)
[x] 10A.4 reranker interface (no-op v1, cross-encoder v1.1)
[x] 10A.5 section_policy_retrieval_node + state
```

**10A total estimate:** ~1,400 lines + migration + docs

---

## Part 4 — Phase 10B: Section-first LLM review

**Detailed implementation plan:** [PHASE10B_SECTION_FIRST_LLM_REVIEW_IMPL_PLAN.md](./PHASE10B_SECTION_FIRST_LLM_REVIEW_IMPL_PLAN.md) (root causes, subtasks, v1.1 hardening)

**Goal:** Full contract section text (no summary) + retrieved policies → LLM compare → merge → final verify.

### 10B.1 — Config & graph mode switch

| Task | Subtasks | Files | Est. lines |
|------|----------|-------|------------|
| 10B.1.1 | `review_pipeline_mode: legacy \| section_first` | `review_agent/config.py` | 10 |
| 10B.1.2 | `section_compare_batch_size=2` | config | 5 |
| 10B.1.3 | `section_compare_max_tokens=48000` — batch budget | config | 5 |
| 10B.1.4 | `section_compare_model` / reuse `compliance_llm_role` | config | 10 |
| 10B.1.5 | `build_review_graph` branch — section_first edges | `review_graph.py` | 80 |
| 10B.1.6 | `.env.example` both modes documented | `review_agent/.env.example` | 15 |

**Graph (section_first):**

```text
clause_detection → index_policies → section_policy_retrieval
  → section_compare_llm → merge_section_findings
  → final_gap_verify → grounding → report → save_memory
```

Skip: `policy_plan`, `policy_retrieval`, `hybrid_*`, `contract_routing`/`policy_discovery` optional (can keep routing for contract_type only).

---

### 10B.2 — Section compare LLM (batched)

| Task | Subtasks | Files | Est. lines |
|------|----------|-------|------------|
| 10B.2.1 Schema | `SectionCompareResult`, `SectionFinding` with required `section_id`, quotes | `schemas/section_compare.py` | 50 |
| 10B.2.2 Prompt | `prompts/section_compare.md` — 2 sections × policies each; JSON array out | prompts | 60 |
| 10B.2.3 Token budget | `estimate_tokens`; if batch > max → batch size 1 | `services/token_budget.py` | 70 |
| 10B.2.4 Service | `compare_section_batch(sections[], retrieval_bundles[])` | `services/section_compare_llm.py` | 180 |
| 10B.2.5 Node | `section_compare_llm_node` — batch loop, concurrency limit | `graph/section_compare_nodes.py` | 90 |
| 10B.2.6 No summary rule | Pass full `section.text`; truncate only if single section > hard cap with warning | service | 20 |
| 10B.2.7 Tests | Mock LLM; 2 sections in one call; findings tagged by section_id | `tests/test_section_compare.py` | 120 |

**Acceptance:** 40 sections, batch 2 → ~20 LLM calls; each finding has `section_id`, `policy_document_id`, quotes.

---

### 10B.3 — Merge section findings

| Task | Subtasks | Files | Est. lines |
|------|----------|-------|------------|
| 10B.3.1 | Dedupe by `(section_id, policy_document_id, dimension_label)` | `services/section_merge.py` | 80 |
| 10B.3.2 | Collect `NO_POLICY_RETRIEVED` sections | same | 30 |
| 10B.3.3 | Collect `UNCLEAR` / low confidence | same | 30 |
| 10B.3.4 | Node `merge_section_findings_node` → `findings`, `gap_sections` | `graph/section_compare_nodes.py` | 50 |
| 10B.3.5 | Map to `ComplianceFinding` for report compatibility | adapter fn | 60 |
| 10B.3.6 | Tests | merge dedupe + gap list | 80 |

---

### 10B.4 — Final gap / verify pass

| Task | Subtasks | Files | Est. lines |
|------|----------|-------|------------|
| 10B.4.1 | Input: gap sections + UNCLEAR findings + policy conflicts | `services/final_verify_llm.py` | 100 |
| 10B.4.2 | Optional broad re-retrieve (`recall_top_k=30`) for NO_POLICY sections | call 10A multi_retrieve | 40 |
| 10B.4.3 | Prompt `prompts/final_gap_verify.md` | prompts | 40 |
| 10B.4.4 | Node `final_gap_verify_node` | graph | 60 |
| 10B.4.5 | Output statuses: `INSUFFICIENT_POLICY`, confirmed NON_COMPLIANT, etc. | schema | 30 |
| 10B.4.6 | Tests | gap section gets final status | 90 |

---

### 10B.5 — Grounding & report (extend, not replace)

| Task | Subtasks | Files | Est. lines |
|------|----------|-------|------------|
| 10B.5.1 | Grounding uses stored section text + policy parent text | existing | 0 |
| 10B.5.2 | Report stats: `sections_reviewed`, `sections_no_policy`, `llm_batches`, `retrieval_paths_used` | `report_node`, generator | 40 |
| 10B.5.3 | Warnings: parser low confidence, batch truncated | merge node | 20 |

---

### 10B.6 — E2E & regression

| Task | Subtasks | Files | Est. lines |
|------|----------|-------|------------|
| 10B.6.1 | `test_review_e2e_section_first.py` — postgres skip pattern | tests | 150 |
| 10B.6.2 | Legacy mode regression — all 52+ tests pass unchanged | CI | 0 |
| 10B.6.3 | Golden fixture: NDA + 2 policies, expect ≥1 NON_COMPLIANT | fixtures | 80 |

---

### 10B summary checklist

```
[x] 10B.1 REVIEW_PIPELINE_MODE + graph branch
[x] 10B.2 section_compare_llm (batch 2, token budget)
[x] 10B.3 merge_section_findings
[x] 10B.4 final_gap_verify (v1 pass-through node)
[x] 10B.5 report stats + grounding unchanged
[x] 10B.6 E2E + legacy regression (requires Postgres for full suite)
```

**10B total estimate:** ~1,500 lines + prompts

---

## Part 5 — Implementation order (sprints)

### Sprint 1 — Foundation (10A.1 + 10A.3 partial)

1. Taxonomy schema + metadata.categories on ingest  
2. Config recall_top_k  
3. FTS-only search path  
4. `multi_retrieval.py` dense + FTS union (no classifier yet — use section title as query)  
5. Unit tests  

**Demo:** Manual script — one section text → union hits  

---

### Sprint 2 — Classifier + metadata path (10A.2 + 10A.3 complete)

1. Section classifier service  
2. Metadata category list search  
3. `section_policy_retrieval_node` (no graph switch yet — callable from test)  
4. Tests  

---

### Sprint 3 — Reranker + graph shell (10A.4 + 10B.1)

1. Reranker no-op + interface  
2. `REVIEW_PIPELINE_MODE=section_first` graph wiring  
3. State fields  
4. Stub compare node (returns empty findings) — graph runs end-to-end  

---

### Sprint 4 — LLM compare + merge (10B.2 + 10B.3)

1. Section compare prompt + service + batching  
2. Merge node  
3. ComplianceFinding adapter  

---

### Sprint 5 — Final verify + E2E (10B.4 + 10B.5 + 10B.6)

1. Final gap verify  
2. Report stats  
3. E2E test + legacy regression  

---

### Sprint 6 — Reranker model + Java categories (optional)

1. Cross-encoder reranker  
2. Java sends categories on sync  
3. Prod `.env.production.example`  

---

## Part 6 — Config reference (new env vars)

### document_core

```env
SEARCH_BACKEND=hybrid
RETRIEVAL_RECALL_TOP_K=20
RETRIEVAL_FINAL_TOP_K=10
RERANKER_ENABLED=false
RERANKER_MODEL=BAAI/bge-reranker-v2-m3
```

### review_agent

```env
REVIEW_PIPELINE_MODE=section_first   # or legacy
SECTION_CLASSIFY_MODE=llm          # or lexical
SECTION_COMPARE_BATCH_SIZE=2
SECTION_COMPARE_MAX_TOKENS=48000
SECTION_RETRIEVAL_CONCURRENCY=8
SECTION_COMPARE_CONCURRENCY=3
REVIEW_MIN_SECTION_CHARS=200
POLICY_SEARCH_TOP_K=20             # recall phase only; final capped by reranker
```

---

## Part 7 — Java / ops dependencies

| Dependency | Owner | Blocks |
|------------|-------|--------|
| Policies indexed from source (not inline text) | Java + ops | 10B prod accuracy |
| `metadata.categories[]` on catalog/register | Java | 10A metadata path |
| PDF/DOCX extract → text (Phase 8) | Java/Python | Parser quality |
| Postgres + pgvector running | ops | all |
| Optional GPU for reranker | ops | 10A.4.3 quality bump |

---

## Part 8 — Risks & mitigations

| Risk | Mitigation |
|------|------------|
| LLM cost (sections × batches) | Batch 2; skip short sections; concurrency cap |
| Classifier wrong categories | Union retrieval + 3 paths; final gap pass |
| 256k context temptation | Hard `SECTION_COMPARE_MAX_TOKENS=48k` per call |
| Cross-section dependencies | Final verify gets conflict list + both section texts |
| Legacy breakage | `REVIEW_PIPELINE_MODE=legacy` default until sign-off |
| No policies indexed | Report `sections_no_policy` + warnings (not silent pass) |

---

## Part 9 — Acceptance criteria (Phase 10 done)

### 10A

- [ ] Union retrieval finds policy chunk missed by dense-only (test proves)  
- [ ] Categories in metadata filter policy docs correctly  
- [ ] Per-section retrieval bundle stored with ≥3 path provenance in meta  

### 10B

- [ ] Full section text sent to compare LLM (no summary field in pipeline)  
- [ ] Batch 2 sections ≈ half LLM calls vs batch 1  
- [ ] Findings include `section_id` + verified quotes after grounding  
- [ ] Sections with no policy hit appear in final gap pass / report warnings  
- [ ] `REVIEW_PIPELINE_MODE=legacy` — all existing tests pass  
- [ ] Contract/policy storage path unchanged (`ingest_document`, `list_sections`)  

---

## Part 10 — File map (new / modified)

### New files

| Path |
|------|
| `document_core/schemas/taxonomy.py` |
| `document_core/migrations/004_policy_categories.sql` |
| `document_core/search/reranker.py` |
| `document_core/search/reranker_service.py` (optional) |
| `review_agent/schemas/section_classify.py` |
| `review_agent/schemas/section_compare.py` |
| `review_agent/services/section_classifier.py` |
| `review_agent/services/multi_retrieval.py` |
| `review_agent/services/section_filter.py` |
| `review_agent/services/section_compare_llm.py` |
| `review_agent/services/section_merge.py` |
| `review_agent/services/final_verify_llm.py` |
| `review_agent/services/token_budget.py` |
| `review_agent/graph/section_retrieval_nodes.py` |
| `review_agent/graph/section_compare_nodes.py` |
| `review_agent/prompts/section_policy_classify.md` |
| `review_agent/prompts/section_compare.md` |
| `review_agent/prompts/final_gap_verify.md` |
| `review_agent/data/policy_category_hints.yaml` |
| `review_agent/tests/test_*` (6 new test modules) |

### Modified files (minimal)

| Path | Change |
|------|--------|
| `review_agent/graph/review_graph.py` | Mode branch |
| `review_agent/config.py` | New settings |
| `review_agent/state/review_state.py` | New state keys |
| `document_core/config.py` | Recall/rerank settings |
| `document_core/store/pgvector_store.py` | FTS-only + category query |
| `document_core/services/search.py` | Export new search fns |
| `document_core/schemas/chunk.py` | categories on ingest |
| `JAVA_CATALOG_API_CONTRACT.md` | categories field |
| `plans/README.md` | Phase 10 entry |

---

## Part 11 — What we explicitly do NOT do in Phase 10

| Item | Reason |
|------|--------|
| Qdrant migration | Postgres hybrid sufficient |
| Remove legacy pipeline | Until QA sign-off |
| Line-by-line compare | Section is the unit |
| Whole contract one LLM call | Context + accuracy |
| Deterministic rule engine | User choice: LLM judgment; keep quote verify only |
| Replace ModernBERT embeddings | Works; reranker is add-on |
| Touch deep_research agent | Out of scope |

---

**Total estimate:** ~2,900 lines + prompts + tests across 10A + 10B (~4–6 dev weeks in sprints above).

*Storage stays the same. Root fix is retrieval recall + contract-section-first compare.*
