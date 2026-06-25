# Phase R2 + R3 — Detailed Implementation Plan (minimal code)

**Scope:** Semantic routing planner (R2) + catalog match (R3).  
**Principle:** LLM proposes **meaning** (intent, concepts, queries); registry + `search_policy_catalog` resolve **document IDs**. No behavior change to section retrieval/compare until `OBLIGATION_ROUTING_ENABLED=true` (ships **false**).

**Depends on:** R0 (`catalog_profile`, `search_policy_catalog`), R1 (`obligations[]`).  
**Estimated LOC:** ~400–500 new, ~60 touched.  
**Duration:** R2 4–5 days · R3 4–5 days.

---

## Root cause (why R2 + R3 exist)

Xecurify failures are **not** compare-model failures. Evidence selection is wrong **before** compare runs.

| Failure | Root cause today | R2/R3 fix |
|---------|------------------|-----------|
| §10.1 Governing Law → Incident Response NON_COMPLIANT | Section classified `governing_law`; retrieval searches by category/embedding; IR doc ranks high on generic `notification`/`security` tags | R1 marks boilerplate → **skip planner + match** (IPC). No IR in candidate set. |
| §10.5 Notices → “Notice Period for Incidents” | Section title `notices` still retrieved against substantive policies; word **notice** collides with incident **notification** | Boilerplate obligation → **no catalog search**. |
| §2.3 mixed section → wrong policy union | `named_policy_routing.py` scans **whole section**; regex list is **hardcoded**, ignores ingest `aliases[]` | Per-obligation planner + **alias match against `catalog_profile`** from registry. |
| `Cyber Defense Manual v14` (future tenant) | Regex/taxonomy cannot map unknown policy names | `search_policy_catalog(query)` over ingest-learned profiles — **no code change**. |
| Planner invents doc IDs | N/A today (risk if added wrong) | LLM output schema **forbids** UUIDs; R3 validates all IDs ∈ tenant registry. |

**Production rule:** Routing proposes; registry validates; catalog match fences; retrieval (R4) only searches inside fence.

---

## Target flow (after R2 + R3)

```text
obligation_extract          (R1, existing)
    │
    ▼
semantic_route              (R2 NEW) — planner LLM OR alias fast-path
    │  outputs: intent, concepts, queries, confidence (NO doc IDs)
    ▼
catalog_match               (R3 NEW) — search_policy_catalog + alias resolve
    │  outputs: candidate_doc_ids[], scores, routing_source
    ▼
contract_routing            (legacy, unchanged when flag off)
policy_discovery            (optional narrow: union candidate_doc_ids when flag on)
section_policy_retrieval    (unchanged until R4)
```

When `OBLIGATION_ROUTING_ENABLED=false`: `semantic_route` + `catalog_match` return `{}` — zero cost, zero risk.

---

## R2 — Semantic routing planner

### Goal

For each **non-boilerplate** obligation, produce a structured routing plan: what the obligation means and what to search for — **never** which document.

### R2.1 — Schema: `ObligationRoutingPlan`

**File:** `review_agent/schemas/routing_plan.py` (~55 LOC)

```python
class ObligationRoutingPlan(BaseModel):
    obligation_id: str
    intent: str = ""                    # e.g. "security incident notification"
    concepts: list[str] = Field(default_factory=list)  # free-form, not taxonomy enum
    search_queries: list[str] = Field(default_factory=list)  # 1-3 retrieval phrases
    explicit_policy_mentions: list[str] = Field(default_factory=list)  # from obligation, echoed
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reasoning: str = ""
    routing_source: Literal["registry_alias", "llm", "skipped_boilerplate"] = "llm"
    # NO document_id, NO policy_ref from LLM
```

**State addition** (`review_state.py`):

```python
obligation_routing_by_id: dict[str, dict[str, Any]]  # obligation_id → plan JSON
```

**Config** (`config.py` + `.env.example`):

```env
SEMANTIC_PLANNER_ENABLED=true          # sub-flag; respects OBLIGATION_ROUTING_ENABLED
SEMANTIC_PLANNER_BATCH_SIZE=5          # obligations per LLM call
SEMANTIC_PLANNER_MAX_OBLIGATION_CHARS=1500
ROUTING_ALIAS_MIN_SCORE=0.92           # alias hit → skip LLM
ROUTING_COMPARE_MIN_CONFIDENCE=0.85    # used in R5; define now
ROUTING_IPC_MAX_CONFIDENCE=0.60
```

---

### R2.2 — Alias fast-path (deterministic, ingest-driven)

**Root cause:** Explicit refs like *"Security Practices Policy"* should not cost an LLM call or hallucinate.

**File:** `review_agent/services/catalog_alias_match.py` (~70 LOC)

```python
def match_explicit_mentions(
    mentions: list[str],
    catalog_entries: list[CatalogEntry],  # from registry
) -> AliasMatchResult | None:
    """
    Match mention text against entry.title + catalog_profile.aliases[] (case-insensitive).
    Returns doc_id + confidence=1.0 when single clear hit.
    """
```

**`CatalogEntry`** (lightweight, built once per review):

```python
@dataclass
class CatalogEntry:
    document_id: str
    policy_ref: str
    title: str
    aliases: list[str]
    topics: list[str]
    summary: str
```

**Load once** in route node:

```python
registry = await client.list_policy_registry(tenant_id, kind="policy")
catalog = [build_catalog_entry(r) for r in registry.policies if r.index_status == "indexed"]
```

**Match rules (minimal):**

1. Normalize: lowercase, strip punctuation.
2. For each `mention`, score against `title` and each `alias`:
   - exact match → 1.0
   - mention ⊆ alias or alias ⊆ mention → 0.95
3. Single best doc above `ROUTING_ALIAS_MIN_SCORE` → `routing_source=registry_alias`, `confidence=1.0`, **skip LLM**.
4. Multiple docs tie → lower confidence to 0.75, still run planner (ambiguous).

**Replaces for explicit refs:** `named_policy_routing.resolve_named_policy_doc_ids` title-substring hack — keep old module for section path until R4; obligation path uses alias matcher only.

---

### R2.3 — Semantic planner LLM

**Files:**

| File | LOC |
|------|-----|
| `prompts/semantic_routing_planner.md` | prompt |
| `services/semantic_routing_planner.py` | ~110 |

**Prompt input (per batch of obligations):**

```text
Contract type: {contract_type}
Obligation id: 2.3-o1
Text: "Notify customer within 8 hours of a security incident."
Explicit policy mentions: []
```

**LLM output JSON (strict):**

```json
{
  "plans": [
    {
      "obligation_id": "2.3-o1",
      "intent": "security incident notification",
      "concepts": ["incident", "notification", "breach", "customer"],
      "search_queries": [
        "security incident notification timeline",
        "breach customer notification requirements"
      ],
      "confidence": 0.91,
      "reasoning": "Implicit breach notification duty; no explicit policy named."
    }
  ]
}
```

**Hard rules in prompt:**

- Do **not** output `document_id`, `policy_ref`, or policy titles as targets.
- `search_queries` must be searchable phrases (not single words like `security`).
- If obligation is procedural/boilerplate, set `confidence` ≤ 0.3 (R3 will IPC).

**Service API:**

```python
async def plan_obligation_routing(
    obligations: list[ContractObligation],
    *,
    contract_type: str | None,
    catalog_entries: list[CatalogEntry],  # titles only as context, not IDs
    settings: ReviewSettings,
) -> dict[str, ObligationRoutingPlan]:
```

**Batch:** `SEMANTIC_PLANNER_BATCH_SIZE` obligations per call (same pattern as `obligation_extract.py`).

**Fallback:** If LLM fails → derive `search_queries` from `obligation.text` first 12 words + `obligation_type`; `confidence=0.5`.

---

### R2.4 — Universal guards (thin, not tenant rules)

**File:** extend `catalog_alias_match.py` or `routing_guards.py` (~40 LOC)

| Guard | Action |
|-------|--------|
| `obligation.is_boilerplate` | Plan with `routing_source=skipped_boilerplate`, `confidence=0`, empty queries |
| Empty obligation text | Skip |
| Planner `confidence` < `ROUTING_IPC_MAX_CONFIDENCE` | Flag `route_decision=ipc` in plan metadata (R5 uses) |

**Do NOT add** large `governing_law ≠ incident` matrices — R1 boilerplate detection handles §10.1/§10.5.

---

### R2.5 — Graph node

**File:** `review_agent/graph/routing_nodes.py` (~55 LOC)

```python
async def semantic_route_node(state, client) -> dict:
    if not settings.obligation_routing_enabled:
        return {}
    obligations = [ContractObligation.model_validate(o) for o in state.get("obligations") or []]
    catalog = await load_catalog_entries(client, state["tenant_id"])
    plans = {}
    for ob in obligations:
        if ob.is_boilerplate:
            plans[ob.obligation_id] = skipped_plan(ob)
            continue
        alias = match_explicit_mentions(ob.explicit_policy_mentions, catalog)
        if alias and alias.confidence >= settings.routing_alias_min_score:
            plans[ob.obligation_id] = plan_from_alias(ob, alias)
            continue
    remaining = [ob for ob in obligations if ob.obligation_id not in plans]
    if remaining:
        plans.update(await plan_obligation_routing(remaining, catalog=catalog, ...))
    return {"obligation_routing_by_id": {k: v.model_dump(mode="json") for k, v in plans.items()}}
```

**Graph wire** (`review_graph.py`):

```text
obligation_extract → semantic_route → catalog_match → contract_routing
```

---

### R2.6 — Tests

| Test | Assert |
|------|--------|
| `test_alias_match_security_practices` | Mention + ingest alias → conf=1.0, skip LLM |
| `test_planner_schema_no_uuid` | Mock LLM output validated; no doc_id field |
| `test_boilerplate_skips_planner` | §10.1 obligation → `skipped_boilerplate` |
| `test_planner_implicit_incident` | "notify within 8 hours" → concepts include incident/notification |
| `test_graph_node_flag_off` | `OBLIGATION_ROUTING_ENABLED=false` → `{}` |

---

### R2 done when

- [ ] Plans in `obligation_routing_by_id` for Xecurify obligations (flag on)
- [ ] Explicit Security Practices mention → `registry_alias`, no LLM
- [ ] §10.1/§10.5 → skipped, no planner call
- [ ] Section retrieval **unchanged** when flag off

---

## R3 — Catalog match

### Goal

Convert each routing plan → **top-K `candidate_doc_ids`** (tenant-scoped only) using `search_policy_catalog` + alias resolution.

### R3.1 — Schema: `CatalogMatchResult`

**File:** `review_agent/schemas/routing_plan.py` (same file, +40 LOC)

```python
class CatalogMatchResult(BaseModel):
    obligation_id: str
    candidate_doc_ids: list[str] = Field(default_factory=list)
    candidate_scores: dict[str, float] = Field(default_factory=dict)  # doc_id → score
    routing_source: str = ""           # registry_alias | catalog_search | ipc
    confidence: float = 0.0
    queries_used: list[str] = Field(default_factory=list)
    rejected: list[dict[str, str]] = Field(default_factory=list)  # {document_id, reason}
    route_decision: Literal["compare", "ipc", "expand"] = "compare"
```

**State addition:**

```python
obligation_catalog_match_by_id: dict[str, dict[str, Any]]
```

---

### R3.2 — `catalog_matcher.py` (~130 LOC)

**File:** `review_agent/services/catalog_matcher.py`

```python
async def match_obligation_to_catalog(
    plan: ObligationRoutingPlan,
    *,
    client: DocumentMCPClient,
    tenant_id: str,
    catalog_entries: list[CatalogEntry],
    allowed_doc_ids: set[str] | None,  # tenant fence
    settings: ReviewSettings,
) -> CatalogMatchResult:
```

**Algorithm (production, minimal):**

```text
1. If plan.routing_source == skipped_boilerplate OR confidence < ROUTING_IPC_MAX:
       → route_decision=ipc, candidate_doc_ids=[]

2. If plan.routing_source == registry_alias:
       → candidate_doc_ids = [alias.doc_id], confidence=1.0, skip search

3. Else catalog search:
       queries = plan.search_queries or [plan.intent]
       For each query q (max 3):
           hits = await client.search_policy_catalog(
               CatalogSearchRequest(tenant_id=tenant_id, query=q, top_k=settings.catalog_match_top_k)
           )
       Union hits by document_id, keep max score per doc

4. Tenant fence:
       all doc_ids MUST be in allowed_doc_ids (from registry indexed policies)
       Drop others → rejected[{id, reason: "not_in_tenant_registry"}]

5. Top-K cap:
       Sort by score desc, take catalog_match_max_candidates (default 5)

6. Confidence adjust:
       If top score < catalog_match_min_score (0.25): route_decision=ipc
       If 0.60 <= confidence < 0.85: route_decision=expand (R5 uses; store now)

7. Return CatalogMatchResult
```

**Why union queries:** Planner may emit `"breach notification"` + `"incident response timeline"` — one query alone misses IR doc.

**Config:**

```env
CATALOG_MATCH_TOP_K=8              # per query
CATALOG_MATCH_MAX_CANDIDATES=5     # final fence size
CATALOG_MATCH_MIN_SCORE=0.25
```

---

### R3.3 — Load tenant fence once

**File:** `review_agent/services/catalog_registry.py` (~45 LOC)

```python
async def load_catalog_entries(client, tenant_id: str) -> list[CatalogEntry]:
    response = await client.list_policy_registry(tenant_id, kind="policy")
    return [
        build_catalog_entry(r)
        for r in response.policies
        if r.index_status == "indexed"
    ]

def indexed_doc_id_set(entries: list[CatalogEntry]) -> set[str]:
    return {e.document_id for e in entries}
```

Shared by R2 alias match + R3 fence — **one registry call per review**.

---

### R3.4 — Graph node + narrow discovery

**File:** `routing_nodes.py` — `catalog_match_node` (~50 LOC)

```python
async def catalog_match_node(state, client) -> dict:
    if not settings.obligation_routing_enabled:
        return {}
    catalog = await load_catalog_entries(client, state["tenant_id"])
    allowed = indexed_doc_id_set(catalog)
    matches = {}
    for oid, raw in (state.get("obligation_routing_by_id") or {}).items():
        plan = ObligationRoutingPlan.model_validate(raw)
        matches[oid] = await match_obligation_to_catalog(plan, client=client, catalog_entries=catalog, allowed_doc_ids=allowed, ...)
    # Optional minimal hook: union candidates for downstream discovery
    union_ids = sorted({doc_id for m in matches.values() for doc_id in m.candidate_doc_ids})
    updates = {"obligation_catalog_match_by_id": {k: v.model_dump(mode="json") for k, v in matches.items()}}
    if union_ids:
        updates["obligation_routing_candidate_doc_ids"] = union_ids
    return updates
```

**Optional narrow `policy_discovery`** (minimal, ~15 LOC in `discovery_nodes.py`):

When `obligation_routing_enabled` and `obligation_routing_candidate_doc_ids` present:

- Seed `discovered_policy_document_ids` with union (intersect request scope if `policy_document_ids` set)
- **Do not** run topic sweep for those obligations (full topic sweep still runs for sections without obligations — keep until R4)

This pre-stages correct doc set without changing retrieval yet.

---

### R3.5 — Audit trail (minimal)

In `obligation_extract_stats` / `compliance_stats` (extend R1 pattern):

```json
{
  "obligation_routed_count": 12,
  "obligation_alias_hit_count": 3,
  "obligation_ipc_route_count": 4,
  "obligation_catalog_match_avg_candidates": 2.1
}
```

Full per-obligation audit blob deferred to R7 — R2/R3 store raw dicts in state for artifact export later.

---

### R3.6 — Tests

| Test | Assert |
|------|--------|
| `test_catalog_match_alias_path` | Alias plan → exactly 1 candidate, no MCP search call |
| `test_catalog_match_incident_query` | Integration: query "breach notification" → IR doc top-1 |
| `test_catalog_match_tenant_fence` | Hit outside registry dropped |
| `test_catalog_match_boilerplate_ipc` | skipped_boilerplate → empty candidates, ipc |
| `test_catalog_match_union_queries` | 2 queries → union of doc sets |
| `test_governing_law_no_ir` | §10.1 boilerplate → ipc, IR doc not in candidates |

**Golden fixture:** `tests/fixtures/routing_golden.json` — 8 obligations from Xecurify with expected `candidate_doc_ids` or `ipc`.

---

### R3 done when

- [ ] `catalog_matcher` returns ≤5 candidates per obligation
- [ ] Golden: §10.1, §10.5 → IPC, zero IR doc
- [ ] Golden: §2.3 security obligation → Security Practices doc in candidates
- [ ] Golden: explicit mention → single alias candidate
- [ ] `OBLIGATION_ROUTING_ENABLED=false` → section pipeline identical

---

## File change matrix

| File | Phase | Change |
|------|-------|--------|
| `schemas/routing_plan.py` | R2+R3 | **new** |
| `services/catalog_registry.py` | R2+R3 | **new** |
| `services/catalog_alias_match.py` | R2 | **new** |
| `services/semantic_routing_planner.py` | R2 | **new** |
| `services/catalog_matcher.py` | R3 | **new** |
| `prompts/semantic_routing_planner.md` | R2 | **new** |
| `graph/routing_nodes.py` | R2+R3 | **new** |
| `graph/review_graph.py` | R2+R3 | +2 nodes, +2 edges |
| `state/review_state.py` | R2+R3 | +2 fields |
| `config.py`, `.env.example` | R2+R3 | +8 settings |
| `graph/discovery_nodes.py` | R3 | ~15 LOC optional seed |
| `clients/document_client.py` | R3 | already has `search_policy_catalog` (R0) |
| `tests/test_semantic_routing.py` | R2 | **new** |
| `tests/test_catalog_matcher.py` | R3 | **new** |
| `tests/fixtures/routing_golden.json` | R3 | **new** |

**Do NOT touch in R2/R3:** `multi_retrieval.py`, `section_compare_nodes.py`, `named_policy_routing.py` (section path), `obligation_extract.py`.

---

## Graph diff (exact)

```python
# review_graph.py
_add_timed_node(graph, "semantic_route", semantic_route_node, client=client)
_add_timed_node(graph, "catalog_match", catalog_match_node, client=client)

graph.add_edge("obligation_extract", "semantic_route")
graph.add_edge("semantic_route", "catalog_match")
graph.add_edge("catalog_match", "contract_routing")
# remove: obligation_extract → contract_routing direct edge
```

**Initial state** (+2 keys):

```python
"obligation_routing_by_id": {},
"obligation_catalog_match_by_id": {},
```

---

## Interface contract (R3 → R4)

R4 obligation retrieval will consume:

```python
match = CatalogMatchResult.model_validate(state["obligation_catalog_match_by_id"][obligation_id])
if match.route_decision == "ipc":
    # emit IPC finding, skip retrieval
fence = match.candidate_doc_ids  # search only inside these
queries = plan.search_queries
```

---

## Execution order

### R2 (days 1–5)

| Day | Tasks |
|-----|-------|
| 1 | `routing_plan.py` schema + `catalog_registry.py` |
| 2 | `catalog_alias_match.py` + tests |
| 3 | `semantic_routing_planner.py` + prompt |
| 4 | `semantic_route_node` + graph wire |
| 5 | Planner tests + Xecurify obligation dry-run (flag on, no retrieval change) |

### R3 (days 6–10)

| Day | Tasks |
|-----|-------|
| 6 | `CatalogMatchResult` + `catalog_matcher.py` core |
| 7 | `catalog_match_node` + graph wire |
| 8 | Discovery seed hook (optional) + compliance_stats |
| 9 | Golden fixtures + integration tests |
| 10 | Xecurify validation: wrong-policy candidates = 0 on golden set |

---

## What NOT to do

| Anti-pattern | Why |
|--------------|-----|
| LLM outputs `document_id` | Hallucination risk; registry is sole ID source |
| Replace `section_policy_retrieval` in R2/R3 | Too big a blast radius; R4 scoped retrieval |
| Expand regex in `named_policy_routing.py` | Use ingest `aliases[]` instead |
| Per-obligation `list_policy_registry` call | One load per review |
| Taxonomy enum in planner | Breaks multi-tenant unknown catalogs |
| Force compare when `route_decision=ipc` | R5 responsibility; R3 only sets decision |

---

## Success metrics (CI gates)

| Gate | Target |
|------|--------|
| Golden wrong-policy candidate | **0** (§10.1, §10.5 never IR) |
| Alias routing recall (explicit refs) | **100%** on fixture |
| Planner calls skipped for boilerplate | **100%** |
| `candidate_doc_ids` ⊆ tenant registry | **100%** |
| Flag off regression | pytest e2e acme unchanged |

---

## Immediate first PR (R2 only, mergeable)

1. `routing_plan.py` + `catalog_registry.py`  
2. `catalog_alias_match.py` + tests  
3. `semantic_route_node` (alias path + boilerplate skip only; planner stub with fallback)  
4. Graph wire behind `OBLIGATION_ROUTING_ENABLED=false` default  

PR 2: planner LLM + PR 3: `catalog_matcher.py` + golden tests.

---

## Ops prerequisites

Before testing R2/R3 on Xecurify:

1. R0 backfill complete — all policies have `metadata.catalog_profile` + catalog vectors  
2. `POST /tools/search_policy_catalog` returns IR doc for `"breach notification"`  
3. `OBLIGATION_EXTRACT_ENABLED=true` (R1)  
4. `OBLIGATION_ROUTING_ENABLED=true` (pilot only)

No document-mcp changes required beyond R0.
