# Phase R — AI-First Semantic Routing (Obligation → Catalog → Evidence)

**Version:** 1.0  
**ID:** `DR-PHASE-R`  
**Principle:** Learn policy catalogs at ingest; route obligations by meaning at review; validate with thin universal guards (no tenant-specific rule engine).

**Depends on:** Section-first pipeline (Phase 10), policy registry (`PolicyRegistryRecord`), pgvector ingest, LLM category tagger.

**Replaces over time:** Section-level `named_policy_routing.py` regex list, topic-only `policy_discovery`, unrestricted corpus retrieval.

---

## Target architecture

```text
INGEST (once per policy)
  index_policy
    → text_parser
    → policy_profiler_llm          ← R0
    → catalog_embedding            ← R0
    → save registry.metadata + catalog_vector
    → chunk embed (existing)

REVIEW (per contract)
  contract_parser
    → obligation_extract_llm       ← R2
    → semantic_routing_planner     ← R3
    → catalog_match                ← R4
    → universal_validation         ← R8 (boilerplate IPC, tenant scope, no invented docs)
    → obligation_retrieval         ← R5 (scoped hybrid: BM25 + vector + rerank)
    → evidence_sufficiency         ← R6
    → obligation_compare_llm       ← R7
    → merge_findings
    → grounding
    → report (+ routing audit)     ← R8
```

---

## Success metrics (Xecurify + synthetic catalogs)

| Metric | Baseline | Target after R |
|--------|----------|----------------|
| Wrong-policy compare (golden set) | §10.1, §10.5, etc. | **0** |
| Explicit named-ref routing recall | partial (regex) | **100%** via ingest aliases |
| False NON_COMPLIANT from bad routing | ~3 | **0** |
| Weighted alignment score | ~57 | **≥75** |
| IPC (correct boilerplate + no playbook) | ~37% (mixed noise) | **15–25%** real gaps only |
| Routing audit on every finding | no | **yes** |

---

## Phase R0 — Policy Profiler at ingest (foundation)

**Goal:** Each tenant policy has a searchable **parent-level profile** — no assumed taxonomy or families.  
**Estimated:** 1–1.5 weeks. **Blocks:** all review routing work.

### R0.1 — Schema: `PolicyCatalogProfile`

| Task | Detail |
|------|--------|
| Define Pydantic model | `policy_ref`, `document_id`, `tenant_id`, `title`, `summary` (2–4 sentences), `topics[]` (free-form strings), `keywords[]`, `obligation_types_covered[]`, `aliases[]` (title variants + explicit names), `catalog_version`, `profiled_at` |
| Store location | `PolicyRegistryRecord.metadata["catalog_profile"]` + dedicated DB column/table for vector |
| Versioning | Bump `catalog_version` on re-index / content_hash change |

**Files:** `document_core/schemas/policy_catalog.py` (new), extend `registry.py`

### R0.2 — Policy Profiler LLM (ingest-time AI #1)

| Task | Detail |
|------|--------|
| Prompt | `document_core/prompts/policy_profiler.md` — input: title + first N chars + section headings outline; output: structured profile JSON |
| Service | `document_core/services/policy_profiler.py` — call after parse, before/at chunk save |
| Wire ingest | `ingest.py` / `index_policy` tool — run profiler when `kind=policy` |
| Config | `POLICY_PROFILER_ENABLED`, `POLICY_PROFILER_MODE=auto|llm|off`, max chars |
| Fallback | Title-only keyword profile when LLM unavailable (degraded, flagged in metadata) |

**Verify:** Re-sync Xecurify 5 policies → each has `summary`, `topics`, `aliases` in registry metadata.

### R0.3 — Catalog embedding index

| Task | Detail |
|------|--------|
| Table | `policy_catalog_vectors(tenant_id, document_id, policy_ref, embedding vector, profile_text, catalog_version)` |
| Embed text | Concat: `title + summary + topics + keywords` |
| MCP tool | `POST /tools/search_policy_catalog` — hybrid: embedding + BM25 on profile_text |
| Backfill script | Profile + embed all indexed policies for tenant |

**Files:** `pgvector_store.py`, document-mcp route, migration SQL

### R0.4 — Demote fixed taxonomy at catalog level (not remove yet)

| Task | Detail |
|------|--------|
| Keep section `categories[]` | Still used for chunk retrieval hints |
| Catalog routing | Uses **free-form `topics[]`** from profiler, not `STANDARD_POLICY_CATEGORIES` only |
| Document | Note in plan: section tagger remains; catalog match does not require taxonomy enum |

### R0.5 — Tests & ops

| Task | Detail |
|------|--------|
| Unit | `test_policy_profiler.py` — mock LLM, schema validation |
| Integration | Sync one weird-named policy (`Cyber Defense Manual v14`) → searchable by "incident notification" query |
| Ops | Re-index all tenant policies after deploy; restart document-mcp |

**Phase R0 done when:**

- [ ] All tenant policies have `catalog_profile` in registry
- [ ] `search_policy_catalog` returns correct doc for semantic query
- [ ] `catalog_version` increments on re-sync

---

## Phase R1 — Obligation model & extraction

**Goal:** Split contract sections into **obligations** (spanned text units) — the unit of routing and compare.  
**Estimated:** 1–1.5 weeks.

### R1.1 — Schema: `ContractObligation`

| Task | Detail |
|------|--------|
| Fields | `obligation_id`, `section_id`, `text`, `char_start`, `char_end`, `obligation_type` (enum-ish free text), `is_boilerplate` (bool), `explicit_policy_mentions[]` |
| State | Add `obligations[]`, `obligation_routing_by_id`, `obligation_retrieval_by_id` to `ReviewState` |
| IDs | `{section_id}-o{index}` |

**Files:** `review_agent/schemas/obligation.py`, `review_state.py`

### R1.2 — Obligation extraction LLM

| Task | Detail |
|------|--------|
| Prompt | `review_agent/prompts/obligation_extract.md` — per section or batched sections |
| Service | `review_agent/services/obligation_extract.py` |
| Rules in prompt | Split on duties/requirements; keep incorporation-by-reference as one obligation; mark boilerplate types |
| Fallback | 1 obligation = full section body if LLM fails |
| Batch | `obligation_extract_batch_size` config (e.g. 3 sections/call) |

### R1.3 — Boilerplate fast-path (universal, not tenant rules)

| Task | Detail |
|------|--------|
| Types | `governing_law`, `notices`, `counterparts`, `severability`, `entire_agreement`, `assignment` (configurable list) |
| Behavior | `is_boilerplate=true` → skip catalog match + retrieval → IPC at compare stage |
| Detection | LLM obligation_type + lexical title hints (`_NOTICE_TITLE` pattern reuse) |

**Verify:** §10.1, §10.5 → boilerplate obligations, zero IR policy hits.

### R1.4 — Graph node

| Task | Detail |
|------|--------|
| Node | `obligation_extract_node` after `contract_parser` / `clause_detection` |
| Feature flag | `OBLIGATION_ROUTING_ENABLED=false` (default off until R7 cutover) |

### R1.5 — Tests

| Task | Detail |
|------|--------|
| Unit | Mixed section 2.3 → ≥2 obligations with different topics |
| Unit | Governing law section → 1 boilerplate obligation |
| Golden | Fixture: Xecurify §2.3, §10.1, §10.5 |

**Phase R1 done when:**

- [ ] Obligations extracted with stable IDs and char spans
- [ ] Boilerplate obligations flagged correctly on golden sections
- [ ] Feature flag off — existing pipeline unchanged

---

## Phase R2 — Semantic routing planner (review-time AI #2)

**Goal:** LLM understands obligation **meaning** and outputs search intent — never document IDs.  
**Estimated:** 1 week.

### R2.1 — Schema: `ObligationRoutingPlan`

```json
{
  "obligation_id": "2.3-o1",
  "intent": "security incident notification",
  "concepts": ["incident", "notification", "breach"],
  "search_queries": ["security incident notification timeline", "breach customer notification"],
  "explicit_policy_mentions": [],
  "confidence": 0.95,
  "reasoning": "..."
}
```

| Task | Detail |
|------|--------|
| Pydantic model | `review_agent/schemas/routing_plan.py` |
| Prompt | `review_agent/prompts/semantic_routing_planner.md` |
| Service | `review_agent/services/semantic_routing_planner.py` |
| Batch | Multiple obligations per LLM call |
| Skip | Boilerplate obligations — no planner call |

### R2.2 — Explicit mention boost (ingest-driven, not regex)

| Task | Detail |
|------|--------|
| On planner output | If `explicit_policy_mentions[]` non-empty → fuzzy match against catalog `aliases[]` + `title` |
| Match fn | `match_explicit_mentions_to_catalog(mentions, registry_profiles)` |
| Confidence | 1.0 when alias hit; attach `routing_source: registry_alias` |
| Skip planner | Optional: if regex-free fuzzy match confident, skip LLM for that obligation |

### R2.3 — Config

| Task | Detail |
|------|--------|
| Settings | `semantic_planner_enabled`, `semantic_planner_batch_size`, `routing_compare_min_confidence` (0.85), `routing_ipc_max_confidence` (0.60) |

### R2.4 — Tests

| Task | Detail |
|------|--------|
| Unit | "notify within 8 hours" → concepts include incident/notification |
| Unit | "Security Practices Policy" → explicit mention + alias match |
| Unit | Planner output never contains UUIDs |

**Phase R2 done when:**

- [ ] Planner produces valid plans for golden obligations
- [ ] Explicit mentions resolve via ingest aliases

---

## Phase R3 — Catalog match & candidate discovery

**Goal:** Map planner intent → **top-K policy doc_ids** from tenant catalog only.  
**Estimated:** 1 week.

### R3.1 — Catalog matcher service

| Task | Detail |
|------|--------|
| Service | `review_agent/services/catalog_matcher.py` |
| Inputs | `ObligationRoutingPlan`, tenant catalog profiles |
| Steps | ① explicit alias boost ② `search_policy_catalog` per query ③ union + score ④ top-K (default 5, max 15) |
| Output | `CatalogMatchResult`: `candidate_doc_ids[]`, `scores[]`, `rejected[]` with reasons |
| Fence | Never return doc_id outside tenant registry |

### R3.2 — Replace section-level discovery for obligation path

| Task | Detail |
|------|--------|
| When `OBLIGATION_ROUTING_ENABLED` | Skip broad `policy_discovery` topic sweep for routed obligations |
| Aggregate | Union of all obligation candidates = session policy scope for preflight |
| Fallback | If catalog match returns 0 candidates and confidence ≥ 0.6 → widen to full tenant catalog search once |

### R3.3 — Optional: soft policy graph (v1 simple)

| Task | Detail |
|------|--------|
| v1 | Cosine similarity between catalog embeddings → `related_doc_ids` as retrieval expand |
| v2 | LLM `relationships[]` from profiler (defer) |
| Expand | Only when evidence sufficiency fails (Phase R4) |

### R3.4 — Tests

| Task | Detail |
|------|--------|
| Unit | Query "breach notification" → Incident Response doc in top-3 (Xecurify) |
| Unit | Governing law obligation → no candidates or empty after validation |
| Unit | Weird catalog names (`Cyber Defense Manual`) → match by profile not title regex |

**Phase R3 done when:**

- [ ] Catalog match returns ≤15 docs per obligation
- [ ] Wrong-family docs excluded on golden set

---

## Phase R4 — Scoped obligation retrieval

**Goal:** Retrieve evidence **only inside candidate doc_ids** using planner queries.  
**Estimated:** 1 week.

### R4.1 — Adapt `multi_retrieval` for obligations

| Task | Detail |
|------|--------|
| Service | `review_agent/services/obligation_retrieval.py` (wrap/adapt `multi_retrieval.py`) |
| Input | obligation text, `search_queries[]`, `candidate_doc_ids[]` |
| Search | BM25 + dense + metadata per query; union; rerank; top-K chunks |
| Remove | Section-level named_policy regex path when obligation routing on |

### R4.2 — Graph node

| Task | Detail |
|------|--------|
| Node | `obligation_retrieval_node` replaces `section_policy_retrieval_node` when flag on |
| State | `obligation_retrieval_by_id` |

### R4.3 — Relevance filter (existing, obligation-scoped)

| Task | Detail |
|------|--------|
| Reuse | `retrieval_relevance.py`, `policy_coverage.py` — pass obligation categories from planner `concepts` |
| Tune | Lower reliance on fixed taxonomy overlap; use concept token overlap |

### R4.4 — Tests

| Task | Detail |
|------|--------|
| Unit | Retrieval never queries doc_id outside candidates |
| Integration | §2.3 security obligation → Security Practices chunks only |

**Phase R4 done when:**

- [ ] Zero retrieval from non-candidate docs
- [ ] Hybrid paths (dense+FTS) working per obligation

---

## Phase R5 — Evidence sufficiency loop

**Goal:** Don't compare until evidence is good enough; expand or IPC otherwise.  
**Estimated:** 3–5 days.

### R5.1 — Sufficiency evaluator

| Task | Detail |
|------|--------|
| Service | `review_agent/services/evidence_sufficiency.py` |
| Signals | hit count, max relevance score, concept overlap, candidate coverage |
| Decisions | `compare` \| `expand_search` \| `ipc` |
| Thresholds | Config: `evidence_min_hits`, `evidence_min_score`, `evidence_expand_max_rounds` (1–2) |

### R5.2 — Expand search path

| Task | Detail |
|------|--------|
| On expand | ① add related catalog docs (embedding neighbors) ② broaden queries ③ relax category filter |
| Cap | Max 1 expand round default |
| Still fail | Emit `INSUFFICIENT_POLICY_CONTEXT` with reason `evidence_insufficient` |

### R5.3 — Confidence gating

| Task | Detail |
|------|--------|
| routing confidence ≥ 0.85 | compare if evidence ok |
| 0.60–0.85 | expand then compare or IPC |
| < 0.60 | IPC without compare |

### R5.4 — Tests

| Task | Detail |
|------|--------|
| Unit | 0 hits → IPC, no compare LLM call |
| Unit | Weak single hit → expand once |
| Golden | Notices obligation → IPC, never IR compare |

**Phase R5 done when:**

- [ ] Compare skipped when evidence insufficient
- [ ] §10.5-style false violations eliminated

---

## Phase R6 — Obligation compare & graph cutover

**Goal:** Compare one obligation ↔ scoped evidence; merge to section/report level.  
**Estimated:** 1–1.5 weeks.

### R6.1 — Obligation compare LLM

| Task | Detail |
|------|--------|
| Service | `review_agent/services/obligation_compare_llm.py` (adapt `section_compare_llm.py`) |
| Prompt | `review_agent/prompts/obligation_compare.md` |
| Input | single obligation + hits + optional related obligations (cross-ref) |
| Batch | Group obligations with same candidate doc set |

### R6.2 — Finding merge

| Task | Detail |
|------|--------|
| Service | `obligation_merge.py` — roll up obligation findings → section findings |
| Dedupe | Reuse `finding_dedupe.py`, `equivalence_guard.py`, `incorporation_guard.py` |
| Metadata | Attach `obligation_id`, routing audit on each finding |

### R6.3 — Graph rewiring

| Task | Detail |
|------|--------|
| New path | `obligation_extract` → `semantic_route` → `catalog_match` → `obligation_retrieval` → `evidence_sufficiency` → `obligation_compare` → `merge` → `grounding` → `report` |
| Flag | `OBLIGATION_ROUTING_ENABLED=true` for pilot tenant |
| Legacy | Keep section path behind flag until R9 validation |

### R6.4 — Deprecate (after validation)

| Task | Detail |
|------|--------|
| Remove | `named_policy_routing.py` regex list (replace with ingest aliases) |
| Slim | `contract_routing_node` — optional or feed obligation extract only |
| Slim | topic-only `policy_discovery` for obligation path |

**Phase R6 done when:**

- [ ] End-to-end review on Xecurify with obligation path
- [ ] Weighted alignment ≥70 on first pilot

---

## Phase R7 — Validation, audit trail & safety

**Goal:** Universal guards + lawyer-facing explainability.  
**Estimated:** 3–5 days.

### R7.1 — Validation layer (thin, tenant-agnostic)

| Task | Detail |
|------|--------|
| `tenant_doc_exists` | Every candidate/doc_id ∈ registry |
| `no_invented_policies` | Planner/matcher cannot add unknown refs |
| `boilerplate_ipc` | Universal obligation types → no compare |
| `permission` | tenant_id scoping on all catalog/search calls |

### R7.2 — Routing audit artifact

| Task | Detail |
|------|--------|
| Per obligation | `routing_source`, `confidence`, `candidate_docs`, `rejected_docs[]`, `queries[]`, `catalog_version` |
| Per finding | Link `obligation_id` + audit blob in `review_artifact` |
| Report | Optional appendix: "Why this policy was selected" |

**Files:** `review_artifact.py`, `schemas/review_artifact.py`

### R7.3 — Grounding (keep, extend)

| Task | Detail |
|------|--------|
| Quote verify | Against obligation `char_span` in section where possible |
| Existing | `grounding_node`, `quote_validate.py` — wire obligation context |

**Phase R7 done when:**

- [ ] Every finding has routing audit JSON
- [ ] Validation rejects out-of-catalog doc_ids

---

## Phase R8 — Golden tests & CI regression

**Goal:** Ship gate on **routing accuracy**, not only end score.  
**Estimated:** 3–5 days.

### R8.1 — Golden routing fixture pack

| Case | Expected |
|------|----------|
| §10.1 governing law | boilerplate → IPC; **never** Incident Response |
| §10.5 notices | boilerplate → IPC; **never** Incident Response |
| §2.3 security measures | Security Practices (or explicit alias) |
| §3.1 / §3.2 retention | Data Retention |
| §5.2 human rights | Code of Conduct |
| Explicit "Security Practices Policy" | alias conf=1.0 |
| Synthetic: `Cyber Defense Manual` | match by profile for incident obligation |

**Files:** `tests/fixtures/routing_golden.json`, `tests/test_routing_golden.py`

### R8.2 — CI gates

| Task | Detail |
|------|--------|
| pytest | `test_routing_golden.py` — mandatory pass |
| Metric | `wrong_policy_compare_count == 0` on fixture set |
| Regression | Xecurify assessment export diff in CI (optional) |

### R8.3 — Weird catalog synthetic tenant

| Task | Detail |
|------|--------|
| Fixture tenant | 5 policies with non-standard names |
| Test | Full review without code changes to routing |

**Phase R8 done when:**

- [ ] CI fails on §10.1 → IR regression
- [ ] 20+ golden cases green

---

## Phase R9 — Caching, cost, metrics & rollout

**Goal:** Production ops at scale.  
**Estimated:** 1 week.

### R9.1 — Caching

| Task | Detail |
|------|--------|
| Catalog profiles | Cache in memory per tenant + `catalog_version` |
| Routing plans | Cache `(tenant, catalog_version, obligation_hash)` |
| Embeddings | Reuse catalog embeddings; don't re-embed per review |

### R9.2 — Cost controls

| Task | Detail |
|------|--------|
| Batch | Obligation extract + planner batched |
| Skip LLM | Alias hit → skip planner; boilerplate → skip all |
| Limits | `max_obligations_per_review`, `max_planner_calls` |

### R9.3 — Metrics & dashboard

| Task | Detail |
|------|--------|
| Counters | `routing_alias_hit`, `routing_planner_call`, `catalog_match_empty`, `evidence_ipc`, `wrong_policy_blocked` |
| Latency | Per-phase timings in `compliance_stats` |
| Export | Assessment JSON includes routing summary |

### R9.4 — Rollout

| Task | Detail |
|------|--------|
| Flag | `OBLIGATION_ROUTING_ENABLED` per tenant in config |
| Pilot | `e2e-demo` / Xecurify tenant first |
| Ops | Re-profile all policies (R0 backfill) before enabling |
| Doc | Update `.env.example`, Dev UI notes |

**Phase R9 done when:**

- [ ] Pilot tenant on obligation path in production
- [ ] Metrics visible; weighted alignment ≥75

---

## File change matrix (expected)

| Area | Files | Phase |
|------|-------|-------|
| Ingest profiler | `policy_profiler.py`, `policy_catalog.py`, migration | R0 |
| Catalog search | document-mcp tool, `pgvector_store.py` | R0 |
| Obligation schemas | `obligation.py`, `review_state.py` | R1 |
| Obligation extract | `obligation_extract.py`, prompt | R1 |
| Semantic planner | `semantic_routing_planner.py`, prompt | R2 |
| Catalog match | `catalog_matcher.py` | R3 |
| Obligation retrieval | `obligation_retrieval.py` | R4 |
| Evidence sufficiency | `evidence_sufficiency.py` | R5 |
| Obligation compare | `obligation_compare_llm.py`, prompt | R6 |
| Graph | `review_graph.py`, new nodes | R1–R6 |
| Audit | `review_artifact.py` | R7 |
| Tests | `test_routing_golden.py`, fixtures | R8 |
| Config | `config.py`, `.env.example` | R0–R9 |

---

## Execution order (summary)

```text
R0  Policy Profiler + catalog embeddings     (1–1.5 wk)  ← START HERE
R1  Obligation extraction + boilerplate       (1–1.5 wk)
R2  Semantic routing planner                  (1 wk)
R3  Catalog match                             (1 wk)
R4  Scoped obligation retrieval               (1 wk)
R5  Evidence sufficiency                      (3–5 d)
R6  Obligation compare + graph cutover        (1–1.5 wk)
R7  Validation + audit trail                  (3–5 d)
R8  Golden tests + CI                         (3–5 d)
R9  Caching, metrics, rollout                 (1 wk)
```

**Total estimate:** ~8–10 weeks sequential; R1–R3 can overlap after R0; R8 runs in parallel from R3 onward.

---

## What NOT to do

- Do **not** build a large per-tenant incompatibility rule matrix — use boilerplate obligation types + evidence gates instead.
- Do **not** let planner output document UUIDs — only catalog match resolves IDs.
- Do **not** remove section-level path until R8 golden set is green.
- Do **not** skip R0 — routing quality ceiling = ingest profile quality.
- Do **not** chase low IPC by forcing compare — correct IPC is success.

---

## Immediate next step

**R0.1 + R0.2 + R0.3:** Policy Profiler schema, LLM at ingest, `search_policy_catalog` MCP tool, backfill Xecurify tenant.
