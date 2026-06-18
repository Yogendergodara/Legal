# Phase 5 — Hybrid Batch Compliance (Align → Batch LLM → Gap Retrieve)

**Plan ID:** `DR-PHASE-5`  
**Status:** Implemented (core — prescreen, batch LLM, gap pass, parallel retrieval)  
**Prerequisite:** Phase 1–3 (done); Phase 4 pgvector (optional but recommended for hybrid search gate)  
**Default:** `COMPLIANCE_MODE=hybrid` (new); legacy `llm` / `lexical` preserved  
**Risk class:** **High** — legal compliance outputs; require grounding, fail-safe defaults, audit metadata  

---

## 1. Executive summary

Replace **one LLM call per policy section** with a **production hybrid pipeline**:

1. **Align** — retrieve policy section + matching contract section per category (parallel, no LLM).  
2. **Pre-screen** — lexical / optional embedding / optional NLI gate; skip obvious pairs.  
3. **Pass 1** — batched LLM compare (5–8 categories per call) with aligned pairs + contract context.  
4. **Gap gather** — collect findings where policy text is missing or confidence is low.  
5. **Gap retrieve** — batch `search_policy` / `get_policy_by_ref` / catalog fetch for gaps only.  
6. **Pass 2** — small batched LLM (or NLI) compare **only gaps**; merge into report.  
7. **Grounding** — unchanged strict quote validation.

**Principles (non-negotiable for legal):**

| Principle | Rule |
|-----------|------|
| Policy is law | Judge only against retrieved policy text; never invent requirements |
| Verbatim quotes | `contract_quote` / `policy_quote` must be exact substrings (existing grounding) |
| Fail-safe | Pre-screen / NLI `NEUTRAL` → LLM; never auto-`COMPLIANT` on low confidence |
| Scope | `REVIEW_POLICY_SCOPE=request` — never widen to full tenant in batch prompts |
| Backward compat | `COMPLIANCE_MODE=llm` keeps current per-section behavior |
| Audit | Every finding carries `compliance_pass`, `retrieval_method`, `prescreen_decision` in metadata |

**Target outcomes:**

| Metric | Current (`llm` per section) | Target (`hybrid`) |
|--------|------------------------------|-------------------|
| LLM calls / 20 categories | ~20 | ~4 Pass 1 + ~0–2 Pass 2 |
| Input tokens / review | ~130k (repeated contract) | ~40–60k |
| Wall-clock (20 categories) | ~60s serial | ~15–25s (parallel + batch) |
| False auto-pass rate | N/A | &lt;2% on golden set (gate must be conservative) |

---

## 2. Root cause

| Symptom | Cause today |
|---------|-------------|
| High token cost | Same contract section context repeated in up to 30 LLM calls |
| High latency | Serial `for category` in `policy_retrieval_node` and `compliance_review_node` |
| Retrieval scores unused | `RetrievalHit.score` not used before LLM |
| No gap loop | Catalog fetch only in retrieval ladder, not when LLM flags missing policy |
| Whole-contract single call rejected | LegalOn-style precision needs focused pairs — but **batched pairs** are OK |

---

## 3. Target architecture

### 3.1 Graph (new)

```text
load_memory
  → index_policies
  → contract_parser
  → clause_detection
  → policy_plan
  → policy_retrieval          # Phase 5A: parallel gather (async)
  → compliance_prescreen      # Phase 5B: NEW node (lexical / optional NLI)
  → compliance_review_pass1   # Phase 5C: NEW — batched LLM
  → policy_gap_retrieval      # Phase 5D: NEW — conditional (skip if no gaps)
  → compliance_review_pass2   # Phase 5D: NEW — gaps only
  → grounding
  → report
  → save_memory
```

**Feature flag:** When `COMPLIANCE_MODE=llm|lexical`, skip new nodes and use existing `compliance_review_node` (adapter in graph builder).

### 3.2 Data flow

```text
review_categories[]
       │
       ▼
policy_retrieval (parallel)
       │
       ├── policy_hits_by_category{}
       ├── contract_hits_by_category{}
       └── alignment_map{ category_id → AlignmentRecord }
       │
       ▼
compliance_prescreen
       │
       ├── prescreen_resolved[]     → findings (no LLM)
       └── prescreen_deferred[]     → Pass 1 batches
       │
       ▼
compliance_review_pass1 (batched LLM)
       │
       ├── pass1_findings[]
       └── gap_requests[]           → needs_policy=true
       │
       ▼
policy_gap_retrieval (parallel, batch queries)
       │
       └── gap_hits_by_request_id{}
       │
       ▼
compliance_review_pass2 (batched LLM or NLI)
       │
       └── pass2_findings[]
       │
       ▼
merge(prescreen_resolved + pass1 + pass2) → findings[] → grounding
```

### 3.3 Alignment record (new schema)

```python
class AlignmentRecord(BaseModel):
    category_id: str
    policy_document_id: UUID | None
    policy_section_id: str | None
    policy_hit_score: float
    contract_hit_score: float
    combined_score: float          # weighted retrieval scores
    policy_text_excerpt: str       # truncated for batch prompt
    contract_text_excerpt: str
    retrieval_method: str
```

---

## 4. Configuration

**File:** `review_agent/config.py`, `review_agent/.env.example`

| Env | Default | Purpose |
|-----|---------|---------|
| `COMPLIANCE_MODE` | `hybrid` (new default after Phase 5) | `hybrid` \| `llm` \| `lexical` |
| `COMPLIANCE_BATCH_SIZE` | `6` | Categories per Pass 1/2 LLM call |
| `COMPLIANCE_PREScreen_ENABLED` | `true` | Lexical gate before LLM |
| `COMPLIANCE_PREScreen_COMPLIANT_MIN` | `0.35` | Relative overlap ratio (see `compliance.py`) |
| `COMPLIANCE_PREScreen_NONCOMPLIANT_MAX` | `0.05` | Below → NON_COMPLIANT without LLM |
| `COMPLIANCE_PREScreen_AMBIGUOUS_BAND` | `0.05–0.35` | Defer to LLM |
| `COMPLIANCE_RETRIEVAL_SCORE_MIN` | `0.15` | Min `RetrievalHit.score` to attempt prescreen |
| `COMPLIANCE_GAP_PASS_ENABLED` | `true` | Pass 2 for `needs_policy` |
| `COMPLIANCE_GAP_MAX_RETRIES` | `1` | Max Pass 2 rounds (avoid loops) |
| `COMPLIANCE_LLM_CONCURRENCY` | `3` | Semaphore for parallel batch LLM calls |
| `COMPLIANCE_RETRIEVAL_CONCURRENCY` | `10` | Semaphore for parallel category retrieval |
| `COMPLIANCE_CONTRACT_CONTEXT_MODE` | `aligned` | `aligned` \| `section_group` \| `full` |
| `COMPLIANCE_CONTRACT_FULL_MAX_CHARS` | `40000` | Cap when `full` mode |
| `COMPLIANCE_CROSS_ENCODER_ENABLED` | `false` | Phase 5E optional |
| `COMPLIANCE_NLI_ENABLED` | `false` | Phase 5E optional |
| `COMPLIANCE_NLI_MODEL` | `cross-encoder/nli-deberta-v3-base` | Or legal fine-tune later |

**Rollout:** Ship with `COMPLIANCE_MODE=llm` default unchanged; switch to `hybrid` after golden tests pass.

---

## 5. Detailed subtasks

### Phase 5A — Foundation (state, alignment, flags)

**Goal:** Schemas and state fields without changing runtime behavior.

| ID | Task | Files | Est. | Acceptance |
|----|------|-------|------|------------|
| 5A.1 | Add `AlignmentRecord` schema | `schemas/alignment.py` | 40L | Validates truncation bounds |
| 5A.2 | Add `GapRequest` schema | `schemas/gap_request.py` | 35L | `request_id`, `policy_topic`, `search_queries[]`, `contract_quote`, `category_id?` |
| 5A.3 | Extend `ComplianceLLMResult` for batch + gaps | `schemas/compliance_llm.py` | 50L | See §5A.3 schema below |
| 5A.4 | Extend `ReviewState` | `state/review_state.py` | 30L | New optional keys; no breaking changes |
| 5A.5 | Add config fields | `config.py`, `.env.example` | 40L | All env vars documented |
| 5A.6 | Graph builder flag routing | `graph/review_graph.py` | 60L | `hybrid` vs legacy path compiles |

**5A.3 Batch LLM schema:**

```python
class BatchComplianceItem(BaseModel):
    category_id: str
    status: ComplianceStatus
    severity: Severity = Severity.INFO
    contract_quote: str = ""
    policy_quote: str = ""
    rationale: str
    confidence: float | None = None
    needs_policy: bool = False
    policy_topic: str = ""
    suggested_search_queries: list[str] = Field(default_factory=list)

class BatchComplianceLLMResult(BaseModel):
    items: list[BatchComplianceItem] = Field(..., min_length=1)
```

**5A.4 New state keys:**

```python
alignment_by_category: dict[str, dict]  # serialized AlignmentRecord
prescreen_findings: list[ComplianceFinding]
gap_requests: list[dict]
gap_hits_by_request: dict[str, list[RetrievalHit]]
compliance_pass: str  # "prescreen" | "pass1" | "pass2"
```

**Tests (5A):** Unit tests for schema validation, unknown category_id stripping, empty batch rejected.

---

### Phase 5B — Parallel retrieval + alignment map

**Goal:** Faster retrieval; build alignment map for downstream nodes.

| ID | Task | Files | Est. | Acceptance |
|----|------|-------|------|------------|
| 5B.1 | Extract `resolve_policy_hits` caller to service | `services/policy_retrieval.py` | 30L | No logic change |
| 5B.2 | Add `resolve_all_policy_hits()` with `asyncio.gather` | `services/policy_retrieval.py` | 80L | All categories resolved concurrently |
| 5B.3 | Semaphore wrapper | `services/async_limits.py` | 40L | `COMPLIANCE_RETRIEVAL_CONCURRENCY` respected |
| 5B.4 | Build `alignment_by_category` from hits | `services/alignment.py` | 100L | Truncate excerpts (`COMPLIANCE_MAX_SECTION_CHARS`) |
| 5B.5 | Wire `policy_retrieval_node` | `graph/nodes.py` | 50L | Outputs alignment map + existing hit dicts |
| 5B.6 | Optional: top-k contract hits for rerank | `services/alignment.py` | 40L | Keep top 3 per category in metadata only (5E uses later) |

**5B.2 Implementation sketch:**

```python
async def resolve_all_policy_hits(categories, ..., semaphore):
    async def one(cat):
        async with semaphore:
            return cat.category_id, *await resolve_policy_hits(...)
    results = await asyncio.gather(*(one(c) for c in categories), return_exceptions=True)
    # fail-soft: per-category errors → warning + empty hits
```

**Tests (5B):**

- `test_parallel_retrieval_all_categories` — 5 categories, mock client call count = 5, wall time &lt; serial (mock delay).
- `test_alignment_map_truncation` — long section → excerpt ≤ max chars.
- `test_retrieval_failure_one_category` — one exception → warning, others succeed.

---

### Phase 5C — Pre-screen gate (no LLM)

**Goal:** Resolve obvious pairs without LLM; defer ambiguous to Pass 1.

| ID | Task | Files | Est. | Acceptance |
|----|------|-------|------|------------|
| 5C.1 | Extract shared overlap logic | `services/compliance_prescreen.py` | 60L | Refactor from `compliance.py` thresholds |
| 5C.2 | Add retrieval score guard | same | 30L | Skip prescreen if `combined_score < RETRIEVAL_SCORE_MIN` → defer |
| 5C.3 | Conservative NON_COMPLIANT rule | same | 40L | Only if **no** contract hit OR overlap &lt; 0.05 |
| 5C.4 | Never auto-COMPLIANT without policy+contract hits | same | 20L | Legal safety |
| 5C.5 | `compliance_prescreen_node` | `graph/nodes.py` | 50L | Splits deferred vs resolved |
| 5C.6 | Metadata on findings | same | 20L | `prescreen_decision`, `overlap_score` |

**Pre-screen decision table (conservative):**

| Condition | Decision |
|-----------|----------|
| No policy hits | `INSUFFICIENT_POLICY_CONTEXT` — resolved, no LLM |
| No contract hits | `INCONCLUSIVE` or gap request — defer Pass 1 |
| overlap &lt; 0.05 | `NON_COMPLIANT` — resolved (existing lexical rule) |
| overlap ≥ 0.35 × policy_self_score | `COMPLIANT` — resolved |
| else | **defer** to Pass 1 LLM |

**Tests (5C):**

- Golden pairs from `fixtures.py` — compliant / non-compliant / ambiguous.
- Assert ambiguous never auto-COMPLIANT.
- `COMPLIANCE_PRESCREEN_ENABLED=false` → all deferred.

---

### Phase 5D — Batch LLM Pass 1

**Goal:** One LLM call per batch of deferred categories.

| ID | Task | Files | Est. | Acceptance |
|----|------|-------|------|------------|
| 5D.1 | Prompt `compliance_review_batch.md` | `prompts/` | 120L | SYSTEM + USER; N items; quote rules unchanged |
| 5D.2 | `build_pass1_prompt(batch, alignment, contract_context)` | `services/compliance_batch_llm.py` | 150L | Contract context mode respected |
| 5D.3 | `compare_batch_pass1()` | same | 120L | Structured output; per-item quote validation |
| 5D.4 | Batch splitter utility | `services/compliance_batch.py` | 40L | Chunks list by `COMPLIANCE_BATCH_SIZE` |
| 5D.5 | `compliance_review_pass1_node` | `graph/nodes.py` | 80L | Parallel batches via semaphore |
| 5D.6 | Map batch results → `ComplianceFinding` | `services/compliance_batch_llm.py` | 60L | `dimension_id` = `category_id` |
| 5D.7 | Extract `gap_requests` from `needs_policy=true` | same | 40L | Dedupe by `policy_topic` + queries |

**5D.1 Prompt rules (legal):**

- Each item: policy excerpt + contract excerpt + category label only.  
- Do not include other tenant policies.  
- If policy excerpt empty → set `needs_policy=true`; do not guess compliance.  
- Same verbatim quote rules as `compliance_review.md`.

**5D.2 Contract context modes:**

| Mode | Pass 1 includes |
|------|-----------------|
| `aligned` (default) | Only aligned contract excerpts per item |
| `section_group` | Unique contract parent sections for batch (deduped) |
| `full` | Full contract text if ≤ `COMPLIANCE_CONTRACT_FULL_MAX_CHARS` else fallback to `section_group` + warning |

**5D.5 Async pattern:**

```python
batches = chunk(deferred_categories, settings.compliance_batch_size)
sem = asyncio.Semaphore(settings.compliance_llm_concurrency)
async def run_batch(batch):
    async with sem:
        return await compare_batch_pass1(...)
findings = flatten(await asyncio.gather(*(run_batch(b) for b in batches)))
```

**Tests (5D):**

- Mock LLM returns 6 items → 6 findings.
- One item `needs_policy=true` → gap_requests length 1.
- Invalid quote → retry once; then INCONCLUSIVE (existing pattern).
- Batch size 6, 14 deferred → 3 LLM calls.

---

### Phase 5E — Gap retrieval + Pass 2

**Goal:** When LLM identifies missing policy, retrieve then re-compare — gaps only.

| ID | Task | Files | Est. | Acceptance |
|----|------|-------|------|------------|
| 5E.1 | `collect_gap_requests()` dedupe | `services/gap_retrieval.py` | 50L | Merge duplicate topics |
| 5E.2 | `resolve_gap_hits()` parallel search | same | 100L | `search_policy` per query; gather |
| 5E.3 | Catalog fetch for unknown refs | same | 60L | Reuse `index_fetched_policy` if ref in gap metadata |
| 5E.4 | `policy_gap_retrieval_node` | `graph/nodes.py` | 60L | Skip node if `gap_requests` empty |
| 5E.5 | `compare_batch_pass2()` | `services/compliance_batch_llm.py` | 100L | Smaller batches; gap items only |
| 5E.6 | `compliance_review_pass2_node` | `graph/nodes.py` | 50L | Merge into findings |
| 5E.7 | Loop guard | same | 20L | `COMPLIANCE_GAP_MAX_RETRIES=1` — no Pass 3 |

**Gap retrieval ladder (per gap request):**

```text
1. search_policy(suggested_queries) scoped to request policy_document_ids
2. If policy_ref hint → get_policy_by_ref (Phase 5G) or catalog fetch
3. If still empty → finding stays INCONCLUSIVE + warning
```

**Tests (5E):**

- Pass 1 emits 2 gaps → 2 retrieval calls (parallel).
- Gap resolved → Pass 2 upgrades INCONCLUSIVE → COMPLIANT/NON_COMPLIANT.
- Unresolved gap → warning in report metadata.

---

### Phase 5F — Merge, grounding, report

**Goal:** Single findings list; existing grounding unchanged.

| ID | Task | Files | Est. | Acceptance |
|----|------|-------|------|------------|
| 5F.1 | `merge_compliance_findings()` | `services/compliance_merge.py` | 80L | prescreen + pass1 + pass2; dedupe by category_id |
| 5F.2 | Dedup rule | same | 30L | Prefer pass2 &gt; pass1 &gt; prescreen for same category_id |
| 5F.3 | Update `grounding_node` | `graph/nodes.py` | 20L | No change to quote verify logic |
| 5F.4 | Report metadata | `reports/generator.py` | 40L | `llm_calls`, `prescreen_skipped`, `gap_count` in artifacts |
| 5F.5 | Warnings aggregation | `graph/nodes.py` | 30L | Cap warnings, dedupe |

**Tests (5F):**

- Same category in prescreen + pass1 → pass1 wins.
- Grounding still drops bad quotes.

---

### Phase 5G — Python-only policy registry (`get_policy_by_ref`)

**Goal:** Resolve synced policies from DB without Java catalog HTTP.

| ID | Task | Files | Est. | Acceptance |
|----|------|-------|------|------------|
| 5G.1 | `get_document_by_policy_ref()` on store protocol | `document_core/store/memory_store.py` | 40L | Returns `document_id` or None |
| 5G.2 | PgVector implementation | `document_core/store/pgvector_store.py` | 50L | Uses `ix_policy_documents_ref` |
| 5G.3 | MCP tool `POST /tools/get_policy_by_ref` | `mcp/document_server/main.py` | 60L | Request: `tenant_id`, `policy_ref` |
| 5G.4 | Client method | `review_agent/clients/document_client.py` | 30L | |
| 5G.5 | Wire into gap retrieval + index_policies | `services/gap_retrieval.py`, `nodes.py` | 50L | Try DB before catalog HTTP |
| 5G.6 | `register_policy` metadata-only (optional) | `document_core` + MCP | 120L | Register ref/title without full text; status `pending_index` |

**5G.3 Response:**

```json
{
  "tenant_id": "acme",
  "policy_ref": "vendor-msa-v3",
  "document_id": "uuid",
  "title": "...",
  "indexed": true
}
```

**Tests (5G):**

- Index with `policy_ref` → get_by_ref returns same `document_id`.
- Memory store: dict in metadata or skip (pgvector only integration test).

---

### Phase 5H — Optional rerankers (cross-encoder + NLI)

**Goal:** Better alignment and fewer LLM deferrals — **off by default**.

| ID | Task | Files | Est. | Acceptance |
|----|------|-------|------|------------|
| 5H.1 | `CrossEncoderReranker` wrapper | `review_agent/rerank/cross_encoder.py` | 80L | Lazy load `ms-marco-MiniLM-L-6-v2` |
| 5H.2 | Integrate in `alignment.py` | `services/alignment.py` | 50L | Rerank top-3 contract hits |
| 5H.3 | `NLIComplianceJudge` | `review_agent/judges/nli.py` | 120L | entailment/contradiction/neutral |
| 5H.4 | Insert between prescreen and Pass 1 | `compliance_prescreen.py` | 40L | NLI neutral → defer LLM |
| 5H.5 | Optional deps | `review_agent/pyproject.toml` | 10L | `[optional] rerank = ["sentence-transformers>=2.2"]` |

**NLI mapping (conservative):**

| NLI label | Action |
|-----------|--------|
| entailment + high score | COMPLIANT (if quotes extractable) |
| contradiction + high score | NON_COMPLIANT |
| neutral / low score | defer to LLM |

**Tests (5H):** Mock models; one golden entailment pair from fixtures.

---

### Phase 5I — Observability, rollout, docs

| ID | Task | Files | Est. | Acceptance |
|----|------|-------|------|------------|
| 5I.1 | Structured logs per phase | nodes + services | 60L | `category_count`, `batch_count`, `gap_count`, `llm_calls` |
| 5I.2 | Finding metadata contract | schemas | 30L | Documented fields for UI |
| 5I.3 | Update `review/plans/README.md` | README | 20L | Phase 5 entry |
| 5I.4 | Update `review/README.md` | | 40L | COMPLIANCE_MODE=hybrid |
| 5I.5 | Golden test suite | `tests/test_compliance_hybrid_golden.py` | 200L | ≥10 fixture pairs |
| 5I.6 | E2E hybrid path | `tests/test_review_e2e.py` | 80L | `COMPLIANCE_MODE=hybrid` + lexical prescreen in CI |
| 5I.7 | CI conftest | `tests/conftest.py` | 10L | `COMPLIANCE_MODE=lexical` or hybrid+mock LLM |

---

## 6. Implementation order (sprints)

### Sprint 1 — Safe foundation (no behavior change)

- 5A全部  
- 5B parallel retrieval (behavior equivalent, faster)  
- Tests green; `COMPLIANCE_MODE=llm` still default  

### Sprint 2 — Pre-screen + batch Pass 1

- 5C prescreen  
- 5D Pass 1 batching  
- 5F merge (pass1 + prescreen only)  
- Golden tests; flip internal dev to `hybrid`  

### Sprint 3 — Gap loop + registry

- 5E gap retrieval + Pass 2  
- 5G `get_policy_by_ref`  
- E2E with stub catalog  

### Sprint 4 — Optimization + hardening

- 5H cross-encoder / NLI (optional)  
- 5I observability + docs  
- Production default evaluation → `COMPLIANCE_MODE=hybrid`  

---

## 7. Risk register (legal / production)

| Risk | Mitigation |
|------|------------|
| Auto-COMPLIANT false negative | Conservative prescreen; ambiguous → LLM |
| Auto-COMPLIANT false positive | Require both hits + overlap threshold; grounding verifies quotes |
| Batch LLM conflates items | Strict per-item JSON schema; validate item count = batch size |
| Missing policy hallucination | `needs_policy` only when `policy_quote` empty; Pass 2 required |
| Token overflow in batch | Cap excerpts; reduce batch size dynamically if prompt &gt; budget |
| Parallel LLM rate limits | `COMPLIANCE_LLM_CONCURRENCY` semaphore (default 3) |
| Gap infinite loop | `COMPLIANCE_GAP_MAX_RETRIES=1` |
| Tenant policy bleed | `REVIEW_POLICY_SCOPE=request`; search filters `document_id` |
| Regression for CI | Keep `COMPLIANCE_MODE=lexical` in conftest; hybrid tests mock LLM |

---

## 8. Test plan summary

| Layer | Tests |
|-------|-------|
| Unit | prescreen thresholds, batch split, gap dedupe, schema validation |
| Integration | parallel retrieval, batch LLM mock, gap retrieval ladder |
| Golden | 10+ policy/contract pairs with expected status |
| E2E | gateway → review → report with `hybrid` + in-memory store |
| Performance | 20 categories: assert LLM call count ≤ 5 Pass1 + 2 Pass2 |
| Legal safety | No COMPLIANT without policy_quote + contract_quote after grounding |

---

## 9. Files touched (summary)

| Area | New | Modified |
|------|-----|----------|
| Schemas | `alignment.py`, `gap_request.py`, batch LLM schemas | `compliance_llm.py`, `review_state.py` |
| Services | `alignment.py`, `compliance_prescreen.py`, `compliance_batch.py`, `compliance_batch_llm.py`, `gap_retrieval.py`, `compliance_merge.py`, `async_limits.py` | `policy_retrieval.py`, `compliance.py` |
| Graph | 3 new nodes | `review_graph.py`, `nodes.py` |
| Prompts | `compliance_review_batch.md` | — |
| document_core | — | `pgvector_store.py`, `memory_store.py` |
| document-mcp | `get_policy_by_ref` tool | `main.py` |
| Config | — | `config.py`, `.env.example` |
| Tests | `test_compliance_hybrid_*.py`, `test_alignment.py`, `test_gap_retrieval.py` | `test_review_e2e.py`, `conftest.py` |

---

## 10. Acceptance criteria (Phase 5 complete)

- [ ] `COMPLIANCE_MODE=hybrid` produces valid `ReviewReport` on sample MSA + policy fixtures  
- [ ] LLM calls for 20 categories ≤ 6 (Pass 1) + ≤ 2 (Pass 2) in perf test  
- [ ] `COMPLIANCE_MODE=llm` unchanged (regression)  
- [ ] All gap findings either resolved in Pass 2 or marked INCONCLUSIVE with warning  
- [ ] Grounding pass rate ≥ current per-section mode on golden set  
- [ ] `get_policy_by_ref` works on pgvector store  
- [ ] Parallel retrieval reduces mock latency vs serial  
- [ ] Report artifacts include `llm_calls`, `prescreen_skipped_count`, `gap_count`  
- [ ] No tenant-wide policy leak in batch prompts (assertion test)  

---

## 11. Out of scope (Phase 5)

- Java catalog sync implementation  
- Full contract single-shot LLM (rejected for precision)  
- Replacing grounding with LLM self-verify  
- Multi-turn agent loop beyond one gap pass  
- Fine-tuning legal DeBERTa (use pre-trained NLI only in 5H)  

---

## 12. Related plans

| Plan | Relationship |
|------|--------------|
| Phase 3 LLM filter | Complementary — filter categories **before** alignment |
| Phase 4 pgvector | Improves search scores for prescreen gate |
| Phase 2 catalog fetch | Reused in gap retrieval ladder |

---

*Document version: 1.0 — aligned with codebase as of Phase 4 partial.*
