# Phase R4 + R5 — Detailed Implementation Plan (minimal code)

**Scope:** Scoped obligation retrieval (R4) + evidence sufficiency loop (R5).  
**Principle:** Retrieve **only inside R3 fence** using planner queries; **gate compare** until evidence is on-topic and strong enough. Section-first compare stays unchanged until R6.

**Depends on:** R0 (indexed policies), R1 (`obligations[]`), R2 (`obligation_routing_by_id`), R3 (`obligation_catalog_match_by_id`, `route_decision`).  
**Estimated LOC:** ~450–550 new, ~80 touched (mostly extract + graph wire).  
**Duration:** R4 ~1 week · R5 3–5 days.

---

## Root cause (why R4 + R5 exist)

R2/R3 fix **which documents** are candidates. Xecurify false NON_COMPLIANT persists if chunk retrieval and compare still run on **wrong evidence inside the corpus** or **before evidence is good enough**.

| Failure (Xecurify golden) | Root cause after R2/R3 | R4 fix | R5 fix |
|---------------------------|------------------------|--------|--------|
| §10.1 Governing Law → IR chunk in compare | `section_policy_retrieval` still queries **full discovery set** using **section title/category** (`governing_law`); dense path ranks IR on `security`/`notification` tags | Boilerplate obligation: R3 `ipc` → R4 **skips retrieval** entirely | R5 confirms `ipc`, never schedules compare |
| §10.5 Notices → “incident notice period” hit | Section-level FTS on word **notice** collides with incident **notification** across all discovered docs | Obligation-scoped query + **hard `document_ids` fence** from R3 | Weak/off-topic hits → `ipc` not compare |
| §2.3 security obligation → wrong section of wrong doc | Section retrieval uses **whole section text**; mixed §2.3 pulls retention + security tokens; hits span wrong parents | Per-obligation `search_queries[]` + fence to Security Practices doc only | Concept overlap gate drops retention-only chunks |
| Explicit “Security Practices Policy” → compare anyway with 0 relevant chunks | Alias path resolves doc but section retrieval may still use section classifier categories | Alias obligation: retrieve inside **single doc** with planner intent as query | 0 on-topic hits after relevance filter → `ipc` (not NON_COMPLIANT) |
| High routing confidence but weak retrieval | N/A until R4 | Hybrid dense+FTS+rerank per obligation query | `expand` once (broaden query / neighbor doc) then compare or IPC |
| Compare cost + hallucination on thin evidence | Section compare runs whenever `policy_hits` non-empty | Scoped retrieval raises precision | **Confidence gating**: skip compare LLM when insufficient |

**Production rule:** R3 fences documents → R4 fences chunks inside those documents → R5 fences compare until evidence passes → R6 compares (next phase).

---

## Target flow (after R4 + R5)

```text
… (R0–R3 unchanged)
    │
    ▼
contract_routing → policy_discovery → index_policies   (existing)
    │
    ├──────────────────────────────────────┐
    ▼                                      ▼
obligation_retrieval (R4 NEW)      section_policy_retrieval (legacy, unchanged)
    │  BM25 + dense + rerank inside candidate_doc_ids only
    ▼
evidence_sufficiency (R5 NEW)      (section path continues)
    │  compare | expand (1 round) | ipc
    ▼
section_compare_llm                (unchanged until R6 consumes obligation_evidence_by_id)
```

When `OBLIGATION_ROUTING_ENABLED=false`: `obligation_retrieval` + `evidence_sufficiency` return `{}` — zero cost, zero risk.

When `OBLIGATION_ROUTING_ENABLED=true` but R6 not shipped: obligation evidence is **stored in state + stats**; section compare still runs (legacy). Golden validation uses state assertions, not report findings, until R6 cutover.

---

## R4 — Scoped obligation retrieval

### Goal

For each obligation with `route_decision != ipc`, retrieve top-K **policy chunks** using planner queries, searching **only** `candidate_doc_ids` from R3.

### R4.1 — Schema: `ObligationRetrievalBundle`

**File:** `review_agent/schemas/obligation_retrieval.py` (~45 LOC)

```python
class ObligationRetrievalBundle(BaseModel):
    obligation_id: str
    section_id: str
    candidate_doc_ids: list[str] = Field(default_factory=list)
    policy_hits: list[RetrievalHit] = Field(default_factory=list)
    queries_used: list[str] = Field(default_factory=list)
    concepts: list[str] = Field(default_factory=list)
    retrieval_meta: dict[str, Any] = Field(default_factory=dict)
    skipped_reason: str | None = None  # ipc_preflight | boilerplate | empty_fence
```

**State addition** (`review_state.py`):

```python
obligation_retrieval_by_id: dict[str, dict[str, Any]]
```

**Config** (`config.py` + `.env.example`):

```env
OBLIGATION_RETRIEVAL_ENABLED=true       # sub-flag; respects OBLIGATION_ROUTING_ENABLED
OBLIGATION_RETRIEVAL_CONCURRENCY=4
OBLIGATION_RETRIEVAL_MAX_QUERIES=3      # cap planner queries per obligation
OBLIGATION_RETRIEVAL_UNION_TOP_K=12     # pre-rerank union pool per obligation
# Reuse existing retrieval knobs:
# RETRIEVAL_RECALL_TOP_K, RETRIEVAL_FINAL_TOP_K, RETRIEVAL_MAX_HITS_PER_DOCUMENT
```

---

### R4.2 — Extract shared hybrid retrieval core (minimal touch)

**Problem:** `multi_retrieval.py` is section-centric (classifier, named_policy regex, category hard-filter ladder). Duplicating dense+FTS+rerank is risky.

**Minimal change:** expose existing private `_retrieve_attempt` for obligation use — **no behavior change** to section path.

**File:** `review_agent/services/multi_retrieval.py` (~15 LOC touched)

```python
# Change: _retrieve_attempt → retrieve_hybrid_attempt (public)
async def retrieve_hybrid_attempt(...) -> tuple[list[RetrievalHit], dict[str, Any]]:
    """Dense + FTS + optional metadata paths, union, diverse cap, rerank."""
```

Section `multi_retrieve_for_section` calls `retrieve_hybrid_attempt` unchanged.

---

### R4.3 — `obligation_retrieval.py` (~140 LOC)

**File:** `review_agent/services/obligation_retrieval.py`

```python
async def retrieve_for_obligation(
    client: DocumentMCPClient,
    *,
    obligation: ContractObligation,
    plan: ObligationRoutingPlan,
    match: CatalogMatchResult,
    tenant_id: str,
    contract_type: str | None,
    policy_type: str | None,
    settings: ReviewSettings,
) -> ObligationRetrievalBundle:
```

**Algorithm (production, minimal):**

```text
1. Preflight skip (no MCP):
   - match.route_decision == "ipc" OR plan.routing_source == "skipped_boilerplate"
     → return bundle with skipped_reason="ipc_preflight", policy_hits=[]

2. Fence:
   - candidate_doc_ids = match.candidate_doc_ids (already tenant-validated in R3)
   - if empty → skipped_reason="empty_fence"

3. Query list:
   - queries = unique(plan.search_queries or [plan.intent or obligation.text[:200]])
   - cap at OBLIGATION_RETRIEVAL_MAX_QUERIES

4. Per query q:
   - filter_doc_ids = [UUID(id) for id in candidate_doc_ids]
   - hits_q, step_q = await retrieve_hybrid_attempt(
         client, query=q,
         filter_doc_ids=filter_doc_ids,
         categories=[],                    # NO category hard-filter for obligations
         category_hard_filter=False,
         ...
     )
   - Union hits by chunk_id, keep max score

5. Post-filter (reuse, obligation-scoped):
   - filter_hits_by_relevance(
         hits,
         section_categories=plan.concepts,  # free-form concepts, not taxonomy enum
         section_title=obligation.text[:120],
         min_score=retrieval_relevance_min_score,
         doc_catalog_categories=...,
     )
   - is_incompatible_hit still applies (governing_law ≠ incident categories)

6. Cap:
   - diverse_top_k + final top RETRIEVAL_FINAL_TOP_K

7. retrieval_meta: per-query steps, fence size, relevance_dropped count
```

**Why no category hard-filter:** R3 already selected documents semantically. Category filter caused §10.1-style doc-set pollution; obligation path uses **doc fence + concept relevance** only.

**Why no `named_policy_routing`:** Explicit refs resolved in R2 alias path → single doc in R3 fence.

---

### R4.4 — Graph node

**File:** `review_agent/graph/obligation_retrieval_nodes.py` (~70 LOC)

```python
async def obligation_retrieval_node(state, client) -> dict:
    if not settings.obligation_routing_enabled or not settings.obligation_retrieval_enabled:
        return {}
    obligations = [...]
    plans = state["obligation_routing_by_id"]
    matches = state["obligation_catalog_match_by_id"]
    coros = [retrieve_for_obligation(...) for ob in obligations if ob.obligation_id in matches]
    results = await gather_limited(coros, limit=settings.obligation_retrieval_concurrency)
    return {
        "obligation_retrieval_by_id": {k: v.model_dump(mode="json") for ...},
        "compliance_stats": {..., "obligation_retrieved_count": N, "obligation_retrieval_zero_hit": Z},
    }
```

**Graph wire** (`review_graph.py`):

```text
index_policies → obligation_retrieval → evidence_sufficiency → section_policy_retrieval
```

Section retrieval edge unchanged after sufficiency node.

---

### R4.5 — Tests

| Test | Assert |
|------|--------|
| `test_obligation_retrieval_respects_fence` | Mock MCP: `document_ids` in SearchRequest ⊆ candidates only |
| `test_obligation_retrieval_ipc_skip` | `route_decision=ipc` → no `search_policy_*` calls |
| `test_obligation_retrieval_union_queries` | 2 queries → union chunk set, max score wins |
| `test_obligation_retrieval_boilerplate` | skipped_boilerplate → zero hits |
| `test_governing_law_no_ir_chunks` | Golden §10.1 → ipc preflight, IR doc never queried |
| `test_security_obligation_sp_only` | §2.3-o0 fence = Security Practices doc; hits parent doc_id ∈ fence |
| `test_graph_node_flag_off` | `OBLIGATION_ROUTING_ENABLED=false` → `{}` |

**Golden extension:** add to `tests/fixtures/routing_golden.json`:

```json
{"obligation_id":"10.1-o0","expect_retrieval":"skip_ipc"}
{"obligation_id":"2.3-o0","expect_retrieval":"hits_in_doc":"Security Practices Policy"}
```

---

### R4 done when

- [ ] Zero `search_policy_*` calls with `document_id` outside R3 `candidate_doc_ids`
- [ ] Hybrid paths (dense + FTS + rerank) run per obligation query
- [ ] §10.1 / §10.5 → retrieval skipped (ipc preflight)
- [ ] §2.3 security obligation → hits only from Security Practices doc (integration)
- [ ] Section retrieval **byte-identical** when `OBLIGATION_ROUTING_ENABLED=false`

---

## R5 — Evidence sufficiency loop

### Goal

Decide per obligation whether to **compare**, **expand retrieval once**, or emit **IPC** — combining R3 routing confidence with R4 hit quality.

### R5.1 — Schema: `EvidenceSufficiencyResult`

**File:** `review_agent/schemas/evidence_sufficiency.py` (~50 LOC)

```python
class EvidenceSufficiencyResult(BaseModel):
    obligation_id: str
    decision: Literal["compare", "expand", "ipc"] = "ipc"
    reason: str = ""
    hit_count: int = 0
    max_relevance_score: float = 0.0
    concept_overlap_score: float = 0.0
    candidate_doc_coverage: float = 0.0   # fraction of candidate docs with ≥1 hit
    routing_confidence: float = 0.0
    expand_round: int = 0                 # 0 = initial, 1 = after expand
    final_hits: list[RetrievalHit] = Field(default_factory=list)
```

**State addition:**

```python
obligation_evidence_by_id: dict[str, dict[str, Any]]
```

**Config:**

```env
EVIDENCE_SUFFICIENCY_ENABLED=true
EVIDENCE_MIN_HITS=1
EVIDENCE_MIN_SCORE=0.35                 # max relevance after filter
EVIDENCE_MIN_CONCEPT_OVERLAP=0.25       # token overlap planner concepts ↔ hit text
EVIDENCE_MIN_DOC_COVERAGE=0.0           # 0 = disabled; 0.5 = half candidates must have a hit
EVIDENCE_EXPAND_MAX_ROUNDS=1
EVIDENCE_EXPAND_BROADEN_MODE=concepts   # concepts | catalog_neighbor | both
EVIDENCE_EXPAND_MAX_EXTRA_DOCS=2        # catalog neighbor cap
```

Uses existing `ROUTING_COMPARE_MIN_CONFIDENCE=0.85` and `ROUTING_IPC_MAX_CONFIDENCE=0.60` from R2/R3.

---

### R5.2 — `evidence_sufficiency.py` (~160 LOC)

**File:** `review_agent/services/evidence_sufficiency.py`

```python
def evaluate_evidence_sufficiency(
    *,
    obligation: ContractObligation,
    plan: ObligationRoutingPlan,
    match: CatalogMatchResult,
    bundle: ObligationRetrievalBundle,
    settings: ReviewSettings,
) -> EvidenceSufficiencyResult:
```

**Decision table (deterministic, production):**

```text
1. Routing pre-gate (from R3 + R4):
   - match.route_decision == "ipc" OR bundle.skipped_reason
     → decision=ipc, reason=routing_or_skip

2. Routing confidence gate:
   - plan.confidence < ROUTING_IPC_MAX_CONFIDENCE (0.60)
     → ipc, reason=low_routing_confidence

3. Hit quantity / quality:
   - hit_count < EVIDENCE_MIN_HITS
     → if match.route_decision == "expand" AND expand_round < MAX: decision=expand
     → else ipc, reason=insufficient_hits
   - max_relevance_score < EVIDENCE_MIN_SCORE
     → same expand-or-ipc branch

4. Concept overlap (obligation-specific):
   - overlap = token Jaccard(plan.concepts + obligation.text, hit titles + excerpts)
   - if overlap < EVIDENCE_MIN_CONCEPT_OVERLAP and no high-score hit
     → expand or ipc

5. Compare path:
   - plan.confidence >= ROUTING_COMPARE_MIN_CONFIDENCE (0.85)
     AND hits pass gates
     → decision=compare

6. Middle band (R3 already flagged expand):
   - 0.60 <= confidence < 0.85
     → if hits OK after initial retrieval: compare
     → else: expand once, re-evaluate; still weak → ipc (NOT non-compliant)
```

**IPC is not a violation.** R6 will map `decision=ipc` → `INSUFFICIENT_POLICY_CONTEXT` finding (same as section coverage gate today).

---

### R5.3 — Expand search path (single round)

**File:** `review_agent/services/evidence_sufficiency.py` + call back into `obligation_retrieval.py`

**On `decision=expand`:**

```text
A. Broaden queries (default, no extra MCP catalog):
   - append plan.concepts as extra query terms
   - append obligation_type if substantive

B. Optional catalog neighbors (when EVIDENCE_EXPAND_BROADEN_MODE includes catalog_neighbor):
   - search_policy_catalog(plan.intent, top_k=EVIDENCE_EXPAND_MAX_EXTRA_DOCS)
   - union new doc_ids with existing fence (still tenant-validated)
   - re-run retrieve_for_obligation with expand_round=1

C. Re-evaluate with expand_round=1; if still insufficient → ipc
```

**Cap:** `EVIDENCE_EXPAND_MAX_ROUNDS=1` default — prevents retrieval storms on large contracts.

---

### R5.4 — Graph node

**File:** `review_agent/graph/obligation_retrieval_nodes.py` — `evidence_sufficiency_node` (~60 LOC)

```python
async def evidence_sufficiency_node(state, client) -> dict:
    if not settings.obligation_routing_enabled or not settings.evidence_sufficiency_enabled:
        return {}
    for obligation_id, raw_bundle in state["obligation_retrieval_by_id"].items():
        result = evaluate_evidence_sufficiency(...)
        if result.decision == "expand" and result.expand_round == 0:
            expanded_bundle = await retrieve_for_obligation(..., expand_mode=True)
            result = evaluate_evidence_sufficiency(..., bundle=expanded_bundle, expand_round=1)
        evidence[obligation_id] = result
    return {
        "obligation_evidence_by_id": {...},
        "compliance_stats": {
            "obligation_compare_ready_count": ...,
            "obligation_evidence_ipc_count": ...,
            "obligation_evidence_expand_count": ...,
        },
    }
```

---

### R5.5 — Audit / stats (minimal)

Extend `compliance_stats` / `obligation_extract_stats`:

```json
{
  "obligation_retrieved_count": 8,
  "obligation_retrieval_zero_hit": 1,
  "obligation_compare_ready_count": 5,
  "obligation_evidence_ipc_count": 4,
  "obligation_evidence_expand_count": 2,
  "obligation_evidence_expand_success": 1
}
```

Per-obligation audit blob deferred to R7 — R4/R5 store raw dicts in state for artifact export.

---

### R5.6 — Tests

| Test | Assert |
|------|--------|
| `test_sufficiency_zero_hits_ipc` | 0 hits → ipc, reason=insufficient_hits |
| `test_sufficiency_weak_hit_expand` | `route_decision=expand`, 1 weak hit → expand round 1 called once |
| `test_sufficiency_high_confidence_compare` | conf=0.9, 2 good hits → compare |
| `test_sufficiency_low_confidence_ipc` | conf=0.4 → ipc without expand |
| `test_sufficiency_boilerplate_ipc` | skipped bundle → ipc, no expand |
| `test_notices_never_compare` | §10.5 golden → ipc end-to-end |
| `test_graph_node_flag_off` | flag off → `{}` |

---

### R5 done when

- [ ] Compare-ready obligations have `decision=compare` only when hits + confidence pass
- [ ] §10.5-style false compare path eliminated in obligation state (ipc before R6)
- [ ] Expand runs at most once per obligation
- [ ] Golden: 0 wrong-policy compare scheduling for boilerplate obligations

---

## File change matrix

| File | Phase | Change |
|------|-------|--------|
| `schemas/obligation_retrieval.py` | R4 | **new** |
| `schemas/evidence_sufficiency.py` | R5 | **new** |
| `services/multi_retrieval.py` | R4 | ~15 LOC — export `retrieve_hybrid_attempt` |
| `services/obligation_retrieval.py` | R4 | **new** |
| `services/evidence_sufficiency.py` | R5 | **new** |
| `graph/obligation_retrieval_nodes.py` | R4+R5 | **new** |
| `graph/review_graph.py` | R4+R5 | +2 nodes, +2 edges |
| `state/review_state.py` | R4+R5 | +2 fields |
| `config.py`, `.env.example` | R4+R5 | +12 settings |
| `tests/test_obligation_retrieval.py` | R4 | **new** |
| `tests/test_evidence_sufficiency.py` | R5 | **new** |
| `tests/fixtures/routing_golden.json` | R4+R5 | extend |

**Do NOT touch in R4/R5:** `section_compare_nodes.py`, `section_compare_llm.py`, `section_policy_retrieval_node` logic, `named_policy_routing.py`, `obligation_extract.py`, `catalog_matcher.py` (except tests).

---

## Graph diff (exact)

```python
# review_graph.py
from review_agent.graph.obligation_retrieval_nodes import (
    evidence_sufficiency_node,
    obligation_retrieval_node,
)

_add_timed_node(graph, "obligation_retrieval", obligation_retrieval_node, client=client)
_add_timed_node(graph, "evidence_sufficiency", evidence_sufficiency_node, client=client)

graph.add_edge("index_policies", "obligation_retrieval")
graph.add_edge("obligation_retrieval", "evidence_sufficiency")
graph.add_edge("evidence_sufficiency", "section_policy_retrieval")
# remove: index_policies → section_policy_retrieval direct edge
```

**Initial state** (+2 keys):

```python
"obligation_retrieval_by_id": {},
"obligation_evidence_by_id": {},
```

---

## Interface contract (R5 → R6)

R6 obligation compare will consume:

```python
evidence = EvidenceSufficiencyResult.model_validate(
    state["obligation_evidence_by_id"][obligation_id]
)
plan = ObligationRoutingPlan.model_validate(state["obligation_routing_by_id"][obligation_id])
match = CatalogMatchResult.model_validate(state["obligation_catalog_match_by_id"][obligation_id])

if evidence.decision != "compare":
    # emit IPC finding with evidence.reason + routing audit
    return ipc_finding(obligation, evidence, plan, match)

hits = evidence.final_hits
# compare LLM: obligation.text vs hits (same evidence block shape as section compare)
```

---

## Execution order

### R4 (days 1–5)

| Day | Tasks |
|-----|-------|
| 1 | `obligation_retrieval.py` schema + export `retrieve_hybrid_attempt` |
| 2 | `retrieve_for_obligation` core + fence tests |
| 3 | Relevance filter wiring (concepts as categories) + union query tests |
| 4 | `obligation_retrieval_node` + graph wire |
| 5 | Golden + Xecurify dry-run (flag on, section compare still legacy) |

### R5 (days 6–8)

| Day | Tasks |
|-----|-------|
| 6 | `EvidenceSufficiencyResult` + evaluator (no expand) |
| 7 | Expand round + `evidence_sufficiency_node` + graph wire |
| 8 | Golden IPC/expand tests + stats |

---

## What NOT to do

| Anti-pattern | Why |
|--------------|-----|
| Replace `section_policy_retrieval_node` in R4 | Blast radius; R6 cutover |
| Re-enable category hard-filter for obligations | Recreates §10.1 doc pollution |
| Compare on expand failure | R5 → ipc; R6 emits IPC status |
| Per-obligation `list_policy_registry` | R3 already loaded catalog; pass through state if needed |
| Multiple expand rounds by default | Cost + latency; cap at 1 |
| LLM evidence sufficiency judge | Deterministic gates first; LLM only in R6 compare |

---

## Success metrics (CI gates)

| Gate | Target |
|------|--------|
| Retrieval fence violation | **0** (no chunk search outside `candidate_doc_ids`) |
| Golden boilerplate retrieval calls | **0** MCP search calls |
| Golden §2.3 security hits doc scope | **100%** hits from Security Practices doc |
| `decision=compare` with 0 hits | **0** |
| Expand rounds per obligation | **≤1** |
| Flag off regression | section e2e unchanged |

---

## Ops prerequisites

Before testing R4/R5 on Xecurify:

1. R0 backfill + R2/R3 enabled (`OBLIGATION_ROUTING_ENABLED=true`)
2. Policies indexed (`index_policies` node succeeds)
3. `search_policy_recall` / `search_policy_fts` return chunks for fenced doc IDs
4. Optional: reranker enabled for production quality (`RERANKER_ENABLED=true` in document_core)

No new document-mcp endpoints required.

---

## Immediate first PR (R4 only, mergeable)

1. Export `retrieve_hybrid_attempt` from `multi_retrieval.py`
2. `obligation_retrieval.py` + schema + ipc preflight skip
3. `obligation_retrieval_node` + graph wire behind flags
4. Fence unit tests + golden skip cases

PR 2: R5 evidence sufficiency + expand round + golden IPC tests.
