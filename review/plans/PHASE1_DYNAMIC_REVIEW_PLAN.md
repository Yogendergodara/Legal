# Phase 1 — Dynamic Review Plan

**Plan ID:** `DR-PHASE-1`  
**Status:** Ready to implement  
**Prerequisite:** None  
**Blocks:** Phase 2, Phase 3  

---

## 1. Executive summary

Replace the static `review_dimensions.yaml` loop with a **dynamic `review_categories` list** built from the tenant's **indexed policy corpus**. Categories = policy parent sections (`list_sections`). Rules stay in policy text; YAML becomes opt-in legacy only.

**Principle:** Minimal code — one new graph node, one service, refactor two loops. No new database. No LLM required in Phase 1.

---

## 2. Root cause (code-grounded)

| Symptom | Root cause | File / line |
|---------|------------|-------------|
| Always reviews 5 topics | Hardcoded YAML dimensions | `dimensions/review_dimensions.yaml` |
| Tenant with NDA-only policy still checks data_protection | Global checklist, not tenant corpus | `nodes.py` L76, L114 `load_dimensions()` |
| Topics in policy but not in YAML never reviewed | No discovery from indexed policies | No `policy_plan` node |
| "Dynamic policies" only half true | Policies indexed per request; **checklist** still static | `index_policies_node` OK; retrieval loop wrong |

---

## 3. Solution strategy (best for v1)

### Why section-based plan (not LLM plan)

| Approach | Risk | Verdict |
|----------|------|---------|
| LLM invents categories | Hallucinated topics not in policies | Reject for Phase 1 |
| Fixed YAML | Wrong for multi-tenant | Reject (current bug) |
| **Policy section enumeration** | Zero invented categories; uses existing MCP tools | **Adopt** |

### Algorithm

```text
1. Collect policy document IDs (union, deduped):
     a. indexed_policies[].document_id  (from index_policies_node this run)
     b. request.policy_document_ids     (Phase 2 field; optional in Phase 1)
     c. list_policies(tenant_id)        (already in store / catalog)

2. For each policy doc:
     if contract_type set AND doc.applies_to_contract_types non-empty:
         skip doc if contract_type not in list
     sections = list_sections(document_id, kind=POLICY)
     for each parent section with len(text) >= MIN_CHARS:
         emit ReviewCategory

3. Cap at REVIEW_MAX_CATEGORIES; append warning if truncated

4. policy_retrieval + compliance_review iterate review_categories
```

### What does NOT change

- `compare_sections_llm` / `compliance_review.md` (compare step)
- `grounding_node` behavior
- Graph nodes except: +`policy_plan`, refactor loops
- `ComplianceFinding.dimension_id` **field name** (value source changes)

---

## 4. Target graph

```text
BEFORE:
  clause_detection → policy_retrieval [YAML] → compliance_review [YAML]

AFTER:
  clause_detection → policy_plan [dynamic] → policy_retrieval → compliance_review
```

---

## 5. Detailed subtasks

### 5.1 Schema — `ReviewCategory`

**File (new):** `review_agent/schemas/review_category.py`  
**Est. lines:** ~35  
**Depends on:** none  

```python
class ReviewCategory(BaseModel):
    category_id: str              # f"{policy_document_id}:{section_id}"
    label: str                    # section.title
    policy_document_id: UUID
    policy_section_id: str
    search_queries: list[str]     # [title, label words, section_path]
    review_guidance: str = ""
    source: str = "policy_section"
```

**Rules:**
- `category_id` must be stable across runs for the same doc+section.
- `search_queries`: derive from `title` + first 12 words of body (no LLM).

**Acceptance:** Schema validates; `category_id` unique per section.

---

### 5.2 Config

**File:** `review_agent/config.py`  
**File:** `review_agent/.env.example`  
**Est. lines:** ~15  

| Setting | Default | Purpose |
|---------|---------|---------|
| `review_plan_mode` | `dynamic` | `static` = YAML fallback |
| `review_max_categories` | `30` | Cost cap |
| `review_min_section_chars` | `40` | Skip empty/boilerplate sections |

**Acceptance:** `get_settings()` reads env; tests can override via `monkeypatch` + `cache_clear()`.

---

### 5.3 Extend `index_policies_node` — return ingest metadata

**File:** `review_agent/graph/nodes.py`  
**Est. change:** ~20 lines  

**Root cause:** Plan needs `document_id` per indexed policy; node today returns only `warnings`.

**Change:**

```python
indexed_policies: list[dict] = []
# after each successful index_policy:
indexed_policies.append({
    "document_id": str(result.document_id),
    "title": title,
    "policy_type": ...,
    "applies_to_contract_types": ...,
})
return {"warnings": warnings, "indexed_policies": indexed_policies}
```

**Optional:** Pass `IngestRequest.document_id` when re-indexing known catalog doc (prep for Phase 2).

**Acceptance:** State after `index_policies` contains `indexed_policies` with 1 entry per uploaded policy.

---

### 5.4 Client — `list_policies`

**Files:**
- `review_agent/clients/document_client.py` (~12 lines)
- `legal_ai_platform/mcp/document_client.py` (~12 lines)

**Fact:** MCP server exposes `/tools/list_policies` (`document_server/main.py` L134–143). Clients lack wrapper.

```python
async def list_policies(self, tenant_id: str) -> list[UUID]:
    data = await self._post("/tools/list_policies", {
        "tenant_id": tenant_id, "kind": "policy"
    })
    return [UUID(x) for x in data["document_ids"]]
```

**Why both clients:** Gateway uses platform client; unit tests use review client.

**Acceptance:** Client round-trip against document-mcp in test.

---

### 5.5 Service — `build_review_plan()`

**File (new):** `review_agent/services/policy_plan.py`  
**Est. lines:** ~100–130  

**Signature:**

```python
async def build_review_plan(
    *,
    client: DocumentMCPClient,
    tenant_id: str,
    indexed_policies: list[dict],
    policy_document_ids: list[UUID] | None,
    contract_type: str | None,
    settings: ReviewSettings,
) -> tuple[list[ReviewCategory], list[str]]:  # categories, warnings
```

**Sub-steps:**

| Step | Logic |
|------|-------|
| 5.5.1 | Build `doc_meta: dict[UUID, dict]` from `indexed_policies` |
| 5.5.2 | Union IDs: indexed + `policy_document_ids` + `list_policies(tenant)` |
| 5.5.3 | Filter docs by `contract_type` vs `applies_to_contract_types` at **document** level |
| 5.5.4 | For each doc: `list_sections` → parent sections |
| 5.5.5 | Skip if `len(section.text.strip()) < review_min_section_chars` |
| 5.5.6 | Build `ReviewCategory` per section |
| 5.5.7 | Sort by `(document_id, section_path)` |
| 5.5.8 | If `len > review_max_categories`: truncate + warning |

**Empty store:**

```python
warnings.append(
    f"No policy documents indexed for tenant '{tenant_id}'. "
    "Upload policies or provide policy_document_ids to enable compliance checking."
)
return [], warnings
```

**Static fallback** (`review_plan_mode == "static"`):

**File:** `review_agent/dimensions/loader.py` — add `yaml_to_categories()` adapter mapping YAML → `ReviewCategory` with synthetic `policy_document_id` unset / search-only mode.

**Acceptance:**

| Test | Expected |
|------|----------|
| SAMPLE_POLICY (2 sections) | 2 categories, not 5 |
| Empty store | `[]` categories + warning |
| `static` mode | 5 YAML categories |
| Cap=2, 5 sections | 2 categories + cap warning |

---

### 5.6 State

**File:** `review_agent/state/review_state.py`  
**Est. lines:** ~12  

```python
indexed_policies: list[dict[str, Any]]
review_categories: list[ReviewCategory]  # or serialized dicts
policy_hits_by_category: dict[str, list[RetrievalHit]]
contract_hits_by_category: dict[str, list[RetrievalHit]]
```

**Important:** Do **not** use `Annotated[..., operator.add]` on these fields (unlike `findings` / `warnings`). Single assignment per node.

**Backward compat:** Keep old keys `policy_hits_by_dimension` as aliases only if needed for one release — prefer clean rename in same PR.

**Acceptance:** LangGraph compiles; no reducer merge bugs on dict fields.

---

### 5.7 Graph node — `policy_plan_node`

**Files:**
- `review_agent/graph/nodes.py` (~25 lines)
- `review_agent/graph/review_graph.py` (~8 lines)

```python
async def policy_plan_node(state: ReviewState, client: DocumentMCPClient) -> dict:
    settings = get_settings()
    if settings.review_plan_mode == "static":
        categories, w = yaml_to_categories(load_dimensions())
    else:
        categories, w = await build_review_plan(...)
    return {"review_categories": categories, "warnings": w}
```

**Wiring:**

```python
graph.add_node("policy_plan", partial(policy_plan_node, client=client))
graph.add_edge("clause_detection", "policy_plan")
graph.add_edge("policy_plan", "policy_retrieval")
# remove: clause_detection → policy_retrieval direct edge
```

**Acceptance:** Graph invokes `policy_plan` between clause_detection and policy_retrieval.

---

### 5.8 Refactor `policy_retrieval_node` and `compliance_review_node`

**File:** `review_agent/graph/nodes.py`  
**Est. change:** ~40 lines  

Replace:

```python
for dimension_id, spec in dimensions.items():
```

With:

```python
for category in state.get("review_categories") or []:
    category_id = category.category_id  # or category["category_id"]
    label = category.label
    queries = category.search_queries
    ...
    policy_hits_by_category[category_id] = ...
    contract_hits_by_category[category_id] = ...
```

**Compliance call:**

```python
compare_sections_llm(
    dimension_id=category_id,      # keep param name internally
    dimension_label=category.label,
    ...
    review_guidance=category.review_guidance,
)
```

**Remove:** `from review_agent.dimensions.loader import load_dimensions` from retrieval/compliance (keep in static adapter only).

**Acceptance:** No `load_dimensions()` in hot path when `review_plan_mode=dynamic`.

---

### 5.9 `run_review()` initial state

**File:** `review_agent/graph/review_graph.py`  

Add to `initial` state:

```python
"indexed_policies": [],
"review_categories": [],
"policy_hits_by_category": {},
"contract_hits_by_category": {},
```

**Acceptance:** `run_review()` does not KeyError on new fields.

---

### 5.10 Tests

**Files (new):**
- `tests/test_policy_plan.py` (~120 lines)

**Files (update):**
- `tests/conftest.py` — add `REVIEW_PLAN_MODE=dynamic`, `get_settings.cache_clear()`
- `tests/test_review_e2e.py` — assert dynamic path; `"Limitation of Liability"` still in report (section title from parser)

**Fixtures:** `SAMPLE_POLICY` already has `4. Limitation of Liability` and `5. Indemnification` — parser (`text_parser.py` L11–18) treats these as headings.

**Test matrix:**

| ID | Test | Pass criteria |
|----|------|---------------|
| P1-T1 | `test_build_plan_two_sections` | 2 categories from SAMPLE_POLICY |
| P1-T2 | `test_build_plan_empty_store` | `[]` + warning |
| P1-T3 | `test_static_mode_five_dimensions` | 5 categories from YAML |
| P1-T4 | `test_contract_type_filter` | Doc with `applies_to=["nda"]` skipped for `msa` |
| P1-T5 | `test_category_cap` | Cap 1 → 1 category + warning |
| P1-T6 | `test_review_e2e_dynamic` | Full graph; findings > 0 |

**Run:**

```bash
cd review/review_agent
PYTHONPATH="...document_core;...review_agent;...Legal ai" python -m pytest tests/ -v
```

---

### 5.11 Documentation

**File:** `review/README.md` — update compliance section: dynamic plan default, `REVIEW_PLAN_MODE`.

---

## 6. File change summary

| File | Action | ~Lines |
|------|--------|-------:|
| `schemas/review_category.py` | New | 35 |
| `services/policy_plan.py` | New | 120 |
| `dimensions/loader.py` | Add `yaml_to_categories` | 30 |
| `config.py` | Extend | 12 |
| `.env.example` | Extend | 6 |
| `state/review_state.py` | Extend | 12 |
| `graph/nodes.py` | Modify | 80 |
| `graph/review_graph.py` | Modify | 15 |
| `clients/document_client.py` | `list_policies` | 12 |
| `legal_ai_platform/mcp/document_client.py` | `list_policies` | 12 |
| `tests/test_policy_plan.py` | New | 120 |
| `tests/conftest.py` | Modify | 8 |
| `tests/test_review_e2e.py` | Modify | 10 |
| `README.md` | Modify | 20 |

**Total Phase 1:** ~280 production + ~140 test

---

## 7. Risks and mitigations

| Risk | Mitigation |
|------|------------|
| Too many sections (80+) | Cap + warning; Phase 3 LLM filter optional |
| Parser misses headings | `structure_confidence` in report; improve parser separately |
| Duplicate categories same title across docs | Keep separate `category_id` per doc+section |
| Platform client drift | Update both clients in same PR |

---

## 8. Definition of done

- [ ] `REVIEW_PLAN_MODE=dynamic` is default
- [ ] No `load_dimensions()` in retrieval/compliance when dynamic
- [ ] Categories = policy sections, not YAML count
- [ ] Empty store → report with warning, no exception
- [ ] All `review_agent` tests pass
- [ ] `review_dimensions.yaml` documented as static fallback only

---

## 9. Out of scope (Phase 1)

- Policy catalog fetch (Phase 2)
- `get_section` fast path (Phase 2)
- LLM category filter (Phase 3)
- pgvector / persistent RAG DB
- Renaming `ComplianceFinding.dimension_id`
