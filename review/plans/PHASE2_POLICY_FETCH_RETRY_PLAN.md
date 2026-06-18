# Phase 2 — Policy Fetch & Retry Plan

**Plan ID:** `DR-PHASE-2`  
**Status:** Ready after Phase 1  
**Prerequisite:** Phase 1 (`review_categories`, `indexed_policies`, dynamic loops)  
**Blocks:** Production catalog integration (Java/Drive)

---

## 1. Executive summary

When policy text is **not in the local document store** or **search returns zero hits**, fetch from an external **policy catalog**, index into document-mcp, and **retry retrieval** with a bounded ladder. Use **`get_section` exact path** when Phase 1 already knows `policy_document_id` + `policy_section_id` — faster and fewer false negatives than lexical search alone.

**Principle:** One service (`resolve_policy_hits`), one catalog interface (stub now, HTTP later), extend existing `index_policies_node` — no new graph node.

---

## 2. Root cause (code-grounded)

| Symptom | Root cause | File / line |
|---------|------------|-------------|
| Policy in catalog but not in request → not reviewed | `index_policies_node` only indexes `policy_texts[]` body | `nodes.py` L40–59 |
| Search miss → dead end | Single `search_policy`, no retry | `nodes.py` L85–94 |
| Plan knows exact section but still searches | `get_section` never called in graph | `document_client.py` L89–109 |
| Gateway path missing `get_section` | Platform client lacks method | `legal_ai_platform/mcp/document_client.py` |
| Duplicate fetch in one run | No per-run cache | N/A (new) |
| Re-fetch creates duplicate index entries | No stable `document_id` on catalog ingest | `ingest.py` L18 |

---

## 3. Solution strategy

### Retrieval ladder (optimized, max 3 attempts)

```text
Attempt 0 — EXACT (cheapest)
  policy: get_section(tenant, category.policy_document_id, category.policy_section_id)
  contract: search_contract(primary_query, document_id=contract_id, top_k=3)

Attempt 1 — SEARCH (broader)
  policy: search_policy(primary_query, document_id=policy_doc_id, top_k=5)
          if empty → search_policy(label_only, top_k=8)
  contract: search_contract(label_only, top_k=5)

Attempt 2 — FETCH + RE-INDEX (if policy_ref or missing doc)
  catalog.fetch(policy_ref) → index_policy(stable document_id) → repeat Attempt 0–1

Fail → policy_hits=[] → compliance skips LLM → INSUFFICIENT_POLICY_CONTEXT
```

### Why this order

| Step | Cost | Accuracy |
|------|------|----------|
| `get_section` | 1 store lookup | Deterministic when plan from Phase 1 |
| Lexical search | BM25 in `document_core/search/lexical.py` | Handles contract wording mismatch |
| Catalog fetch | Network (later) | Only when store empty |

Aligns with `PIPELINE_REVIEW_ARCHITECTURE.md` §9.4 retry policy.

---

## 4. Detailed subtasks

### 4.1 Catalog interface

**File (new):** `review_agent/clients/policy_catalog.py`  
**Est. lines:** ~70  

```python
class PolicyDocument(BaseModel):
    ref: str
    title: str
    text: str
    policy_type: str | None = None
    applies_to_contract_types: list[str] = Field(default_factory=list)
    document_id: UUID | None = None  # stable id from catalog if provided
    metadata: dict[str, Any] = Field(default_factory=dict)

class PolicyCatalogClient(Protocol):
    async def fetch_policy(self, tenant_id: str, policy_ref: str) -> PolicyDocument | None: ...

class StubPolicyCatalogClient:
    """In-memory ref → PolicyDocument for tests."""

class HttpPolicyCatalogClient:
    """GET {POLICY_CATALOG_URL}/tenants/{tenant}/policies/{ref} — implement when Java ready."""
```

**Design:** `policy_ref` is opaque `str` — review agent decoupled from Drive/Confluence URI schemes.

**Factory:**

```python
def get_policy_catalog() -> PolicyCatalogClient | None:
    if not settings.policy_catalog_url:
        return None
    return HttpPolicyCatalogClient(settings.policy_catalog_url)
```

**Acceptance:** Stub returns fixture policy; HTTP client mocked in tests.

---

### 4.2 Config

**Files:** `review_agent/config.py`, `.env.example`  
**Est. lines:** ~12  

| Setting | Default | Purpose |
|---------|---------|---------|
| `policy_catalog_url` | `None` | Catalog base URL |
| `policy_fetch_enabled` | `true` | Kill switch |
| `policy_retrieval_max_attempts` | `3` | Ladder cap |
| `policy_search_top_k` | `5` | Search breadth |

---

### 4.3 Request / state fields

**Files:**
- `legal_ai_platform/models/agent.py`
- `review_agent/state/review_state.py`
- `review_agent/graph/review_graph.py`

```python
# AgentRequest + effective_context()
policy_document_ids: list[str] | None = None
policy_refs: list[str] | None = None

# ReviewState
policy_document_ids: list[str]
policy_refs: list[str]
fetched_policy_refs: list[str]  # cache serialized set
policy_ref_by_document_id: dict[str, str]  # optional reverse map
```

**File:** `legal_ai_platform/agents/review/review_agent.py` — pass fields to `run_review()`.

**Acceptance:** `POST /query` with `policy_refs` reaches graph state.

---

### 4.4 Extend `index_policies_node` — prefetch catalog policies

**File:** `review_agent/graph/nodes.py`  
**Est. change:** ~45 lines  

**Before** indexing `policy_texts[]`, process `policy_refs`:

```python
fetched_refs = set(state.get("fetched_policy_refs") or [])
catalog = get_policy_catalog() if settings.policy_fetch_enabled else None

for ref in state.get("policy_refs") or []:
    if ref in fetched_refs:
        continue
    if catalog is None:
        warnings.append(f"policy_ref {ref!r} skipped: no catalog configured")
        continue
    doc = await catalog.fetch_policy(tenant_id, ref)
    if doc is None:
        warnings.append(f"policy_ref {ref!r} not found in catalog")
        continue
    result = await client.index_policy(IngestRequest(
        tenant_id=tenant_id,
        document_id=doc.document_id or uuid5(NAMESPACE_DNS, f"{tenant_id}:{ref}"),
        title=doc.title,
        text=doc.text,
        kind=DocumentKind.POLICY,
        policy_type=doc.policy_type,
        applies_to_contract_types=doc.applies_to_contract_types,
        metadata={"policy_ref": ref, **doc.metadata},
    ))
    indexed_policies.append({...})
    fetched_refs.add(ref)
```

**Stable `document_id`:** Use catalog-provided UUID or deterministic `uuid5` from `tenant_id:policy_ref` — prevents duplicate index on re-fetch (`ingest.py` L18).

**Acceptance:** Stub catalog policy indexed before `policy_plan`; ref not fetched twice in same run.

---

### 4.5 Service — `resolve_policy_hits()`

**File (new):** `review_agent/services/policy_retrieval.py`  
**Est. lines:** ~140  

**Signature:**

```python
async def resolve_policy_hits(
    *,
    client: DocumentMCPClient,
    catalog: PolicyCatalogClient | None,
    tenant_id: str,
    category: ReviewCategory,
    contract_document_id: UUID,
    contract_type: str | None,
    policy_type: str | None,
    fetched_refs: set[str],
    policy_ref_by_doc: dict[str, str],
    settings: ReviewSettings,
) -> tuple[list[RetrievalHit], list[RetrievalHit], dict[str, Any]]:
```

**Policy side logic:**

```python
meta = {"retrieval_attempts": 0, "retrieval_method": None}

# Attempt 0: exact
section = await client.get_section(GetSectionRequest(
    tenant_id=tenant_id,
    document_id=category.policy_document_id,
    section_id=category.policy_section_id,
))
if section:
    hit = RetrievalHit(parent_chunk=section, score=1.0)
    return [hit], contract_hits, {**meta, "retrieval_method": "exact"}

# Attempt 1: search primary then broader
for query in [category.search_queries[0], category.label]:
    hits = await client.search_policy(SearchRequest(..., query=query, document_id=category.policy_document_id))
    if hits:
        return hits, contract_hits, {**meta, "retrieval_method": "search"}

# Attempt 2: fetch if policy_ref known for this doc
ref = policy_ref_by_doc.get(str(category.policy_document_id))
if ref and catalog and ref not in fetched_refs:
    ... fetch + index ...
    # retry exact + search once
```

**Contract side:** Same ladder queries against `contract_document_id` (no fetch).

**Wrap hits:** If exact section found, synthesize `RetrievalHit(score=1.0)`.

**Acceptance metadata keys:** `retrieval_method`, `retrieval_attempts`, `fetched_policy`, `policy_ref`.

---

### 4.6 Platform client parity

**File:** `legal_ai_platform/mcp/document_client.py`  

Add (mirror review client):

- `get_section(request: GetSectionRequest) -> IndexedChunk | None`
- `list_policies(tenant_id: str) -> list[UUID]` (if not done in Phase 1)

**Root cause:** `ReviewAgent` passes platform `DocumentMCPClient` (`review_agent.py` L45). Without `get_section`, Attempt 0 fails silently via `AttributeError` or skip.

**Acceptance:** `test_review_gateway.py` passes with fetch/retry path.

---

### 4.7 Wire `policy_retrieval_node`

**File:** `review_agent/graph/nodes.py`  
**Est. change:** ~35 lines  

Replace inline search with:

```python
catalog = get_policy_catalog()
fetched = set(state.get("fetched_policy_refs") or [])
ref_by_doc = state.get("policy_ref_by_document_id") or {}

for category in state.get("review_categories") or []:
    p_hits, c_hits, meta = await resolve_policy_hits(...)
    policy_hits[category.category_id] = p_hits
    contract_hits[category.category_id] = c_hits
    # optional: stash meta for compliance metadata merge

return {
    "policy_hits_by_category": policy_hits,
    "contract_hits_by_category": contract_hits,
    "fetched_policy_refs": list(fetched),
}
```

---

### 4.8 Compliance metadata merge (minimal)

**File:** `review_agent/graph/nodes.py` or `compliance_llm.py`  
**Est. change:** ~10 lines  

Pass retrieval `meta` into finding:

```python
finding.metadata.update(retrieval_meta)
```

No change to compare logic.

---

### 4.9 Tests

**File (new):** `tests/test_policy_retrieval.py` (~130 lines)

| ID | Test | Pass criteria |
|----|------|---------------|
| P2-T1 | `test_exact_get_section` | `retrieval_method=exact`, no search call |
| P2-T2 | `test_search_fallback` | Exact miss → search hit |
| P2-T3 | `test_fetch_on_miss` | Empty store + stub catalog → indexed → hit |
| P2-T4 | `test_no_double_fetch` | Same ref twice → one catalog call |
| P2-T5 | `test_fetch_disabled` | Warning, no catalog call |
| P2-T6 | `test_all_fail_insufficient` | LLM not invoked |
| P2-T7 | `test_gateway_review_with_refs` | Platform e2e with stub catalog |

**File (update):** `legal_ai_platform/tests/test_review_gateway.py` — optional `policy_refs` case.

---

## 5. File change summary

| File | Action | ~Lines |
|------|--------|-------:|
| `clients/policy_catalog.py` | New | 70 |
| `services/policy_retrieval.py` | New | 140 |
| `graph/nodes.py` | Modify | 80 |
| `config.py` | Extend | 12 |
| `.env.example` | Extend | 8 |
| `state/review_state.py` | Extend | 10 |
| `graph/review_graph.py` | Extend | 15 |
| `models/agent.py` (platform) | Extend | 20 |
| `agents/review/review_agent.py` | Extend | 10 |
| `mcp/document_client.py` (platform) | `get_section` | 25 |
| `tests/test_policy_retrieval.py` | New | 130 |

**Total Phase 2:** ~320 production + ~130 test

---

## 6. Java / Drive integration (later — interface only now)

When Java backend is ready, implement `HttpPolicyCatalogClient`:

```http
GET /api/v1/tenants/{tenant_id}/policies/{policy_ref}
→ 200 { title, text, policy_type, applies_to_contract_types, document_id }
```

Review agent unchanged — only catalog client implementation swaps.

---

## 7. Risks and mitigations

| Risk | Mitigation |
|------|------------|
| Lexical search false negatives | `get_section` first; pgvector later |
| Fetch latency | Only on Attempt 2; cache `fetched_policy_refs` |
| Wrong policy fetched | `policy_ref` explicit in request; catalog owns resolution |
| Duplicate documents in store | Stable `document_id` from catalog or uuid5 |

---

## 8. Definition of done

- [ ] `get_section` used when category has section ids
- [ ] Search retry with broader query before fetch
- [ ] Catalog stub fetch → index → hit in tests
- [ ] No duplicate fetch per `policy_ref` per run
- [ ] `policy_refs` on `POST /query` works via gateway
- [ ] Platform client has `get_section`
- [ ] All tests pass

---

## 9. Out of scope (Phase 2)

- Real Drive/Confluence sync (Java jobs)
- pgvector embeddings
- Multi-hit merge (top 3 policy sections → one finding)
- `POLICY_CONFLICT` detection across docs
