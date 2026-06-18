# Phase 6 — Contract-First Policy Discovery (User Sends Contract Only)

**Plan ID:** `DR-PHASE-6`  
**Status:** Implemented (core)  
**Prerequisite:** Phase 5 hybrid (core), Phase 4 store (optional; memory OK for dev)  
**Product default target:** `REVIEW_POLICY_SOURCE=tenant_auto` + `COMPLIANCE_MODE=hybrid`  
**Java catalog sync:** Out of scope (Phase 8); Python stub catalog OK for tests  

---

## 1. Executive summary

**User sends contract only.** Policies live in tenant DB/RAG (pre-synced). System:

1. **Routes** contract → `contract_type` + `topics[]` (1 small LLM call).  
2. **Discovers** policies from tenant index via search (0 LLM).  
3. **Plans** sections from discovered policies only.  
4. **Compares** via existing hybrid pipeline (prescreen → batch LLM → gap pass).  
5. **Merges** → grounding → report.

**Principle:** Minimal diff — **add 2 graph nodes + config**; **reuse** Phase 5 hybrid, retrieval, grounding. **Do not** rewrite compliance or store layers.

---

## 2. Target graph (full product path)

```text
load_memory
  → contract_parser
  → clause_detection
  → contract_routing          NEW (Pass 1 — 1 LLM)
  → policy_discovery          NEW (Pass 2 — search DB)
  → index_policies            SKIP body if already indexed; metadata only
  → policy_plan               discovered document_ids only
  → policy_retrieval          parallel (hybrid)
  → compliance_prescreen      Phase 5
  → compliance_review_pass1
  → policy_gap_retrieval
  → compliance_review_pass2
  → compliance_hybrid_merge
  → grounding → report → save_memory
```

**Legacy path** (`REVIEW_POLICY_SOURCE=request`): skip `contract_routing` + `policy_discovery`; keep today’s flow.

---

## 3. What to keep vs change vs remove

### 3.1 Keep (reuse as-is)

| Component | Why |
|-----------|-----|
| Phase 5 hybrid nodes | Pass 3–4 compare |
| `policy_retrieval.py` ladder | exact → search → catalog |
| `build_review_plan()` | Sections from policy docs |
| `compliance_review.md` / `compliance_review_batch.md` | Compare prompts |
| `policy_catalog.py` | Gap fetch / optional sync |
| Static YAML mode | CI/dev only (`REVIEW_PLAN_MODE=static`) |
| `COMPLIANCE_MODE=llm` | Backward compat flag |
| Inline `policies[]` | Optional override for `request` source |

### 3.2 Change (behavior / config)

| Current | Phase 6 target |
|---------|----------------|
| Orchestrator requires policies/refs/IDs | Contract-only OK when `tenant_auto` |
| `REVIEW_POLICY_SCOPE=request` default | Product: `discovered` scope (see 6A) |
| `REVIEW_POLICY_SCOPE=tenant` = all `list_policies()` | **Do not use for product** — too broad |
| `index_policies` always re-ingest inline text | Skip ingest when doc already in store |
| Default `COMPLIANCE_MODE=llm` | Product: `hybrid` after Phase 6 tests |
| `policy_plan_llm` filter | Off by default; optional cap after discovery |

### 3.3 Remove / do NOT add

| Item | Action |
|------|--------|
| `list_policies()` union in `tenant_auto` path | **Never** — use discovery search only |
| One LLM with full contract + all playbooks | **Never** |
| New compliance graph for discovery | **No** — 2 nodes only |
| Java sync in Phase 6 | Defer Phase 8 |
| Cross-encoder / NLI | Defer Phase 7 |
| Delete `review_dimensions.yaml` | **Keep** for static CI |
| Delete `compliance_review_node` | **Keep** for `COMPLIANCE_MODE=llm\|lexical` |

### 3.4 Dead / unused cleanup (small)

| Item | Task ID |
|------|---------|
| `policy_retrieval_max_attempts` in config unused | 6Z.1 — wire meta or remove field |
| Grounding silently drops findings | 6Z.2 — add warnings (required for legal) |
| `POLICY_CONFLICT` enum unused | 6Z.3 — leave enum; no logic in Phase 6 |

---

## 4. Configuration

**Files:** `review_agent/config.py`, `.env.example`

| Env | Default (dev) | Product default | Purpose |
|-----|---------------|-----------------|---------|
| `REVIEW_POLICY_SOURCE` | `request` | `tenant_auto` | Contract-only vs user-supplied policies |
| `CONTRACT_ROUTING_MODE` | `llm` | `llm` | `llm` \| `lexical` (lexical = section titles only, no API) |
| `CONTRACT_ROUTING_MAX_CHARS` | `12000` | | Cap contract text in routing prompt |
| `DISCOVERY_MAX_POLICIES` | `8` | | Max policy documents per review |
| `DISCOVERY_TOP_K_PER_TOPIC` | `3` | | search_policy hits per topic |
| `DISCOVERY_MIN_SCORE` | `0.15` | | Drop weak policy hits |
| `REVIEW_POLICY_SCOPE` | extend | `discovered` | `request` \| `tenant` \| `discovered` |
| `COMPLIANCE_MODE` | `llm` | `hybrid` | After Phase 6 QA |

**`review_policy_scope=discovered`:** `_union_document_ids()` uses only `discovered_policies` + `policy_document_ids` from discovery — **not** `list_policies()`.

---

## 5. State fields (add to `ReviewState`)

| Key | Type | Set by |
|-----|------|--------|
| `contract_routing` | `dict` | `contract_routing_node` |
| `discovered_policies` | `list[dict]` | `policy_discovery_node` |
| `discovered_policy_document_ids` | `list[str]` | `policy_discovery_node` |
| `discovery_warnings` | `list[str]` | `policy_discovery_node` |

Reuse existing: `indexed_policies`, `policy_document_ids`, `review_categories`, hybrid fields.

---

## 6. Detailed subtasks

### Sprint 1 — Foundation (no graph behavior change)

#### 6A.1 — Config fields

**File:** `review_agent/config.py`  
**Lines:** ~25  
**Implement:**

```python
review_policy_source: Literal["request", "tenant_auto"] = "request"
contract_routing_mode: Literal["llm", "lexical"] = "llm"
contract_routing_max_chars: int = 12_000
discovery_max_policies: int = 8
discovery_top_k_per_topic: int = 3
discovery_min_score: float = 0.15
review_policy_scope: Literal["request", "tenant", "discovered"] = "request"  # extend
```

**Acceptance:** `get_settings()` loads env; defaults unchanged for CI.

---

#### 6A.2 — `.env.example` block

**File:** `review_agent/.env.example`  
**Add** commented Phase 6 section with all vars above.

---

#### 6A.3 — Schema `ContractRoutingResult`

**File (new):** `review_agent/schemas/contract_routing.py`  

```python
class ContractRoutingResult(BaseModel):
    contract_type: str = ""           # msa, nda, sow, unknown
    topics: list[str] = Field(..., min_length=1, max_length=20)
    section_titles: list[str] = Field(default_factory=list, max_length=50)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
```

**Validation:** Strip empty topics; dedupe case-insensitive; max 20 topics.

---

#### 6A.4 — Schema `DiscoveredPolicy`

**File (new):** `review_agent/schemas/discovered_policy.py`  

```python
class DiscoveredPolicy(BaseModel):
    document_id: str
    title: str = ""
    policy_type: str | None = None
    match_score: float = 0.0
    matched_topics: list[str] = Field(default_factory=list)
    policy_ref: str | None = None
```

---

#### 6A.5 — Extend `ReviewState`

**File:** `review_agent/state/review_state.py`  
Add optional keys from §5.

---

#### 6A.6 — Extend `build_review_plan` scope

**File:** `review_agent/services/policy_plan.py`  
**Change:** ~15 lines in `_union_document_ids` caller:

```python
if settings.review_policy_scope == "discovered":
    store_ids = []  # never list_policies
elif settings.review_policy_scope == "tenant":
    store_ids = await client.list_policies(tenant_id)
else:
    store_ids = []
# always union indexed_policies + policy_document_ids + store_ids
```

When `tenant_auto`, pass `policy_document_ids=state.discovered_policy_document_ids` into `build_review_plan`.

**Acceptance:** Unit test — `discovered` scope never calls `list_policies`.

---

### Sprint 2 — Domain prompt + routing service (Pass 1)

#### 6B.1 — Prompt `contract_routing.md`

**File (new):** `review_agent/prompts/contract_routing.md`  

See **§10** full prompt text (optimized for this project).

---

#### 6B.2 — `build_routing_context()`

**File (new):** `review_agent/services/contract_routing.py`  

**Input:** `contract_text`, `contract_sections: list[IndexedChunk]`, `settings`  

**Logic:**

1. If sections exist → build context from `section_titles` + first 400 chars per section (max 15 sections).  
2. Else truncate `contract_text` to `CONTRACT_ROUTING_MAX_CHARS`.  
3. Append user hint `contract_type` from state if set.

**Output:** single string for USER block.

---

#### 6B.3 — `route_contract_llm()`

**File:** same  
**Implement:**

- Load `contract_routing.md` (SYSTEM + USER split, same pattern as `compliance_llm.py`).  
- `invoke_structured(..., ContractRoutingResult)`.  
- Retries: reuse `compliance_llm_max_retries`.  
- Fail-open: on error → `topics` from section titles or `["limitation of liability", "indemnification", "termination"]` + warning.

---

#### 6B.4 — `route_contract_lexical()` (no LLM fallback)

**File:** same  
**Logic:** Extract topics from section titles + keyword list (`liability`, `indemn`, `confidential`, `termination`, `ip`, `data`).  
**Use when:** `CONTRACT_ROUTING_MODE=lexical` or LLM unavailable.

---

#### 6B.5 — `contract_routing_node`

**File (new):** `review_agent/graph/discovery_nodes.py` (group with discovery)  

```python
async def contract_routing_node(state, client) -> dict:
    # uses state.contract_sections, state.contract_text
    # returns {"contract_routing": result.model_dump(), "warnings": [...]}
```

---

#### 6B.6 — Tests `test_contract_routing.py`

- Mock LLM returns 3 topics → schema valid.  
- Lexical mode returns ≥1 topic from SAMPLE_CONTRACT sections.  
- Fail-open on LLM error.

---

### Sprint 3 — Policy discovery (Pass 2, no LLM)

#### 6C.1 — `discover_policies_from_topics()`

**File (new):** `review_agent/services/policy_discovery.py`  

**Signature:**

```python
async def discover_policies_from_topics(
    client: DocumentMCPClient,
    *,
    tenant_id: str,
    topics: list[str],
    contract_type: str | None,
    policy_type: str | None,
    settings: ReviewSettings,
) -> tuple[list[DiscoveredPolicy], list[str]]:
```

**Per topic:**

```python
hits = await client.search_policy(SearchRequest(
    tenant_id=tenant_id,
    query=topic,
    kind=DocumentKind.POLICY,
    contract_type=contract_type,
    policy_type=policy_type,
    top_k=settings.discovery_top_k_per_topic,
))
```

**Aggregate:**

- Key = `document_id`; score = max hit score across topics.  
- Filter `score >= discovery_min_score`.  
- Sort desc; take top `discovery_max_policies`.  
- Build `DiscoveredPolicy` with `matched_topics`.

**If zero policies:** warning `"No policies discovered for tenant; sync playbook or check index."`

---

#### 6C.2 — Optional registry enrich

**File:** same  
If `get_policy_by_ref` exists (6E), attach `title` from `policy_documents` registry; else use hit parent `title`.

---

#### 6C.3 — `policy_discovery_node`

**File:** `review_agent/graph/discovery_nodes.py`  

**Skip when:** `review_policy_source != "tenant_auto"` OR `policy_texts` / `policy_refs` already in state (explicit override).

**Returns:**

```python
{
  "discovered_policies": [...],
  "discovered_policy_document_ids": [...],
  "policy_document_ids": [...],  # merge for downstream
  "discovery_warnings": [...],
}
```

---

#### 6C.4 — Tests `test_policy_discovery.py`

- Index 2 policies in memory store; contract topics → discovers correct doc.  
- `discovery_max_policies=1` caps results.  
- Empty store → warning, empty list.

---

### Sprint 4 — Graph wiring + index skip

#### 6D.1 — Conditional graph edges

**File:** `review_agent/graph/review_graph.py`  

```python
if settings.review_policy_source == "tenant_auto":
    graph.add_node("contract_routing", ...)
    graph.add_node("policy_discovery", ...)
    graph.add_edge("clause_detection", "contract_routing")
    graph.add_edge("contract_routing", "policy_discovery")
    graph.add_edge("policy_discovery", "index_policies")
else:
    graph.add_edge("clause_detection", "index_policies")
```

**Hybrid compliance:** when `tenant_auto`, prefer `compliance_mode=hybrid` in product env (no code force in dev).

---

#### 6D.2 — `index_policies_node` skip re-ingest

**File:** `review_agent/graph/nodes.py`  
**Change:** For each discovered policy:

- If `document_id` already in store (`list_sections` or registry ping) → append metadata to `indexed_policies` only; **do not** re-fetch text.  
- Inline `policy_texts` loop unchanged for `request` source.

**Minimal:** ~20 lines if/continue before `index_policy` call.

---

#### 6D.3 — `policy_plan_node` pass discovered IDs

**File:** `review_agent/graph/nodes.py`  

```python
policy_document_ids = (
    state.get("discovered_policy_document_ids")
    or state.get("policy_document_ids")
)
```

Pass to `build_review_plan(..., policy_document_ids=policy_document_ids)`.

Set `review_policy_scope=discovered` when `tenant_auto` (in node or config default for product).

---

#### 6D.4 — `run_review()` initial state

**File:** `review_agent/graph/review_graph.py`  
Add empty defaults for `contract_routing`, `discovered_policies`, etc.

---

### Sprint 5 — Platform gateway

#### 6E.1 — Orchestrator contract-only

**File:** `legal_ai_platform/.../orchestrator.py`  

```python
if task_type == "review":
    if settings_or_context_review_policy_source == "tenant_auto":
        if not contract_text:
            raise ReviewPayloadError(...)
        # policies optional
    else:
        # existing: policies OR refs OR ids
```

**Minimal:** Read `review_policy_source` from env on platform OR pass `context.review_policy_source` (prefer env mirror of review agent).

---

#### 6E.2 — `get_policy_by_ref` MCP tool (Python)

**Files:**

- `document_core/store/memory_store.py` — `get_document_by_policy_ref(tenant, ref) -> UUID | None` (memory: scan metadata)  
- `document_core/store/pgvector_store.py` — SQL on `policy_documents`  
- `Legal ai/mcp/document_server/main.py` — `POST /tools/get_policy_by_ref`  
- `review_agent/clients/document_client.py` — client method  

**Used by:** discovery enrich + gap pass (not blocking 6C).

---

#### 6E.3 — Gateway test

**File:** `legal_ai_platform/tests/test_review_gateway.py`  
`POST /query` contract only + `REVIEW_POLICY_SOURCE=tenant_auto` (policies pre-indexed in fixture).

---

### Sprint 6 — Hardening + cleanup

#### 6F.1 — Grounding drop warnings

**File:** `review_agent/graph/nodes.py` `grounding_node`  

```python
if not ok:
    warnings.append(f"finding dropped (grounding failed): {finding.dimension_label}")
```

Append to state `warnings`; include count in report metadata.

---

#### 6F.2 — Report enrich

**File:** `reports/generator.py` + `report_node`  

- Add `policy_title` to finding block if in metadata.  
- Report metadata: `discovered_policy_document_ids`, `contract_routing.topics`, `discovery_warnings`.

---

#### 6F.3 — `policy_title` on findings

**File:** `compliance_batch_llm.py` / merge step  
Copy `title` from `discovered_policies` / `indexed_policies` into `finding.metadata["policy_title"]`.

---

#### 6Z.1 — Remove or wire `policy_retrieval_max_attempts`

**File:** `config.py` — either use in `retrieval_meta` or delete field + `.env.example` line.

---

## 7. LLM call budget (tenant_auto + hybrid)

| Step | LLM calls |
|------|-----------|
| Contract routing | **1** |
| Policy discovery | 0 |
| Policy plan LLM filter | 0 (default off) |
| Hybrid prescreen | 0 |
| Hybrid pass 1 | ceil(deferred / 6) |
| Hybrid pass 2 | 0–2 batches |
| **Typical total** | **~2–5** |

---

## 8. Test plan

| ID | Test |
|----|------|
| T1 | `discovered` scope never calls `list_policies` |
| T2 | Routing LLM mock → 3 topics |
| T3 | Discovery finds indexed policy by topic |
| T4 | E2E contract-only → report with ≥1 finding |
| T5 | `request` path regression (inline policies) |
| T6 | Empty discovery → warning, empty categories |
| T7 | Grounding failure → warning in report |
| T8 | Hybrid graph compiles with `tenant_auto` |

---

## 9. Acceptance criteria (Phase 6 done)

- [ ] `POST /query` with **contract only** works when `REVIEW_POLICY_SOURCE=tenant_auto`  
- [ ] Policies discovered from tenant index, not user upload  
- [ ] `REVIEW_POLICY_SCOPE=discovered` — no full-tenant `list_policies`  
- [ ] Hybrid compare runs on discovered policies  
- [ ] Gap pass still works for `needs_policy`  
- [ ] `request` path unchanged (inline policies / refs)  
- [ ] All existing tests pass + new Phase 6 tests  
- [ ] No silent grounding drops  

---

## 10. Domain prompt — `contract_routing.md` (copy into repo)

```markdown
## SYSTEM

You are a contract triage analyst for an in-house legal compliance system.

Your ONLY job: read the contract excerpt and output which **policy topics** the organization's playbook must be checked against.

**Rules:**
1. Output **topics** as short search phrases (2–8 words), e.g. "limitation of liability", "data processing", "indemnification".
2. Infer **contract_type** if possible: `msa`, `nda`, `sow`, `employment`, `unknown`.
3. List **section_titles** exactly as they appear (for traceability).
4. Do **NOT** judge compliance. Do **NOT** invent policy text. Do **NOT** cite law.
5. Include 5–15 topics for a typical commercial contract; fewer for short NDAs.
6. Always include high-risk topics if present: liability cap, indemnity, IP, confidentiality, termination, data privacy, governing law.

**You are routing retrieval, not performing review.**

---

## USER

### Contract metadata
- **Tenant contract review** (playbook will be retrieved from indexed policies after this step)
- **Hint contract_type (may be empty):** {contract_type_hint}

### Contract content (sections or excerpt)
```
{contract_context}
```

Return structured JSON: contract_type, topics[], section_titles[], confidence.
```

**Token optimization:**

- Prefer `section_titles` + 300-char snippet per section over full document.  
- Cap total `contract_context` at `CONTRACT_ROUTING_MAX_CHARS` (default 12k).  
- Single call; temperature 0.

---

## 11. Implementation order (checklist)

```text
[ ] 6A.1–6A.6  Config + schemas + policy_plan scope
[ ] 6B.1–6B.6  Routing prompt + service + node + tests
[ ] 6C.1–6C.4  Discovery service + node + tests
[ ] 6D.1–6D.4  Graph wire + index skip + plan IDs
[ ] 6E.1–6E.3  Gateway + get_policy_by_ref + test
[ ] 6F.1–6F.3  Grounding warnings + report enrich
[ ] 6Z.1         Config cleanup
```

**Estimated new code:** ~600–900 lines. **No deletion** of Phase 5 / hybrid / legacy modes.

---

## 12. Phase 7+ (out of scope)

| Phase | Content |
|-------|---------|
| 7 | Cross-encoder rerank, NLI gate, embedding routing |
| 8 | Java catalog sync → `policy_documents` registry |
| 9 | `POLICY_CONFLICT` multi-policy logic |

---

*Document version: 1.0 — aligned with Phase 5 hybrid and tenant_auto product flow.*
