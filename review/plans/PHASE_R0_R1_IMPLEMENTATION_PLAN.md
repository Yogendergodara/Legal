# Phase R0 + R1 — Detailed Implementation Plan (minimal code)

**Scope:** Policy Profiler at ingest (R0) + Obligation extraction scaffold (R1).  
**Principle:** Smallest diff that unblocks semantic catalog routing (R2+). No graph behavior change until `OBLIGATION_ROUTING_ENABLED=true`.

**Estimated LOC:** ~450–600 new, ~80 touched.  
**Duration:** R0 4–6 days · R1 4–6 days.

---

## R0 — Policy Profiler at ingest

### Goal

After each policy is indexed, store a **parent-level profile** + **one catalog embedding** searchable via `search_policy_catalog`. Review routing (R2+) uses this instead of regex/taxonomy.

### R0 data model

**`PolicyCatalogProfile`** — stored at `policy_documents.metadata.catalog_profile`:

```json
{
  "summary": "Defines incident handling, breach notification, and customer communication.",
  "topics": ["incident", "breach", "notification", "security"],
  "keywords": ["8 hours", "ISMS", "customer notification"],
  "aliases": ["Incident Response Plan", "IR Plan"],
  "obligation_types": ["incident_notification", "incident_reporting"],
  "profile_text": "Incident Response Plan. Defines incident...",
  "catalog_version": 1,
  "profiler": "llm",
  "profiled_at": "2026-06-25T12:00:00Z"
}
```

| Field | Source |
|-------|--------|
| `profile_text` | `title + summary + topics + keywords` (used for FTS + embed) |
| `aliases` | LLM + always include `title` |
| `catalog_version` | Increment when `content_hash` changes |

**New table** `policy_catalog_vectors` (one row per policy):

```sql
-- migrations/007_policy_catalog_vectors.sql
CREATE TABLE IF NOT EXISTS policy_catalog_vectors (
    tenant_id     TEXT NOT NULL,
    document_id   UUID NOT NULL,
    policy_ref    TEXT,
    profile_text  TEXT NOT NULL,
    embedding     vector(768),
    catalog_version INT NOT NULL DEFAULT 1,
    tsv           tsvector GENERATED ALWAYS AS (to_tsvector('english', profile_text)) STORED,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, document_id)
);
CREATE INDEX IF NOT EXISTS ix_policy_catalog_embedding
    ON policy_catalog_vectors USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS ix_policy_catalog_tsv
    ON policy_catalog_vectors USING GIN (tsv);
```

No separate graph/relationships table in R0.

---

### R0 task breakdown

#### R0.1 — Schema (~40 LOC)

| # | Task | File |
|---|------|------|
| 1 | `PolicyCatalogProfile` Pydantic model + `build_profile_text()` | `document_core/schemas/policy_catalog.py` |
| 2 | `CatalogSearchRequest` / `CatalogSearchHit` | same file |
| 3 | Export from `document_core/schemas/__init__` if used | optional |

```python
class PolicyCatalogProfile(BaseModel):
    summary: str = ""
    topics: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    obligation_types: list[str] = Field(default_factory=list)
    profile_text: str = ""
    catalog_version: int = 1
    profiler: Literal["llm", "keyword", "off"] = "off"
    profiled_at: str = ""
```

---

#### R0.2 — Profiler LLM (~120 LOC)

| # | Task | File |
|---|------|------|
| 1 | Prompt `policy_profiler.md` (SYSTEM/USER, JSON output) | `document_core/prompts/policy_profiler.md` |
| 2 | `profile_policy_tree(tree, *, title, settings)` | `document_core/services/policy_profiler.py` |
| 3 | Reuse `ingest_llm.invoke_structured_json` (same as category_tagger) | existing |
| 4 | **Keyword fallback** when LLM off: title tokenize → topics, title → summary | ~15 LOC in same file |

**Profiler input (minimal):**

- `document_title`
- Section outline: `{section_id, title}` for each parent (no full body — saves tokens)
- First 4000 chars of `canonical_text` as body sample

**Profiler output JSON:**

```json
{
  "summary": "...",
  "topics": ["..."],
  "keywords": ["..."],
  "aliases": ["..."],
  "obligation_types": ["..."]
}
```

**Config** (`document_core/config.py` + `.env.example`):

```env
POLICY_PROFILER_ENABLED=true
POLICY_PROFILER_MODE=auto          # auto | llm | keyword | off
POLICY_PROFILER_MODEL=mistral-small-latest
POLICY_PROFILER_MAX_BODY_CHARS=4000
```

`auto` = LLM if key available, else keyword fallback (mirror `category_tagger_mode`).

---

#### R0.3 — Wire ingest (~35 LOC)

**Single hook** in `ingest.py` after `tag_policy_sections`, before `build_parent_child_chunks`:

```python
if request.kind == DocumentKind.POLICY and settings.policy_profiler_enabled:
    profile, profiler_meta = await profile_policy_tree(tree, document_title=request.title, settings=settings)
    extra_meta["catalog_profile"] = profile.model_dump(mode="json")
    extra_meta.update(profiler_meta)  # profiler: llm|keyword
```

`extra_meta` already merges into chunk `metadata` → `save_document` → `policy_documents.metadata`.

**Skip-reindex path:** In `pgvector_store.save_document`, when `existing_hash == content_hash` (early return ~L149), add:

```python
if metadata.get("catalog_profile") and kind == POLICY:
    upsert_policy_catalog_vector(...)  # profile-only update, no chunk re-embed
```

So backfill can attach profiles without forcing full re-chunk.

---

#### R0.4 — Catalog vector upsert (~80 LOC)

| # | Task | File |
|---|------|------|
| 1 | `upsert_policy_catalog_vector(tenant_id, document_id, policy_ref, profile)` | `pgvector_store.py` |
| 2 | Call from `save_document` after policy_documents upsert | same |
| 3 | `embed_query` / `embed_documents` on `profile_text` (one vector) | `embeddings/service.py` |
| 4 | Add methods to `DocumentStore` protocol + `async_adapter` | protocol files |

---

#### R0.5 — `search_policy_catalog` (~100 LOC)

| # | Task | File |
|---|------|------|
| 1 | `search_policy_catalog(request)` service | `document_core/services/catalog_search.py` |
| 2 | Hybrid: cosine on `policy_catalog_vectors` + `ts_rank` on `profile_text` | pgvector_store |
| 3 | Filter: `tenant_id`, optional `document_ids[]`, `top_k` default 10 | |
| 4 | Return `[{document_id, policy_ref, title, score, summary}]` — no chunk text | |

**MCP route** (`Legal ai/mcp/document_server/main.py`):

```python
@app.post("/tools/search_policy_catalog")
async def search_policy_catalog_tool(request: CatalogSearchRequest) -> dict:
    hits = await search_policy_catalog(request)
    return {"results": [h.model_dump(mode="json") for h in hits]}
```

**Clients** — add method only (no review wiring yet):

- `review_agent/clients/document_client.py`
- `legal_ai_platform/mcp/document_client.py`

**Capabilities:** append `search_policy_catalog` to `MCP_CAPABILITIES` in document_server config.

---

#### R0.6 — Tests (~120 LOC)

| Test | Assert |
|------|--------|
| `test_policy_profiler_keyword_fallback` | No API key → `profiler=keyword`, topics non-empty |
| `test_policy_profiler_llm_mock` | Mock JSON → valid `PolicyCatalogProfile` |
| `test_catalog_vector_upsert` | After index_policy, row in `policy_catalog_vectors` |
| `test_search_policy_catalog_incident` | Query `"breach notification"` → Incident Response doc top-1 (Xecurify fixture) |
| `test_skip_reindex_still_updates_profile` | Same content_hash + new profile in metadata → vector updated |

---

#### R0.7 — Ops

| # | Action |
|---|--------|
| 1 | Run migration `007_policy_catalog_vectors.sql` |
| 2 | Set `POLICY_PROFILER_MODE=llm` in `document_core/.env` |
| 3 | `.\start_document_mcp.ps1 -Replace` |
| 4 | Re-sync Xecurify: `python test_xecurify_policies.py` (sync only) |
| 5 | Verify: `list_policy_registry` → each policy has `metadata.catalog_profile` |

**R0 done when:**

- [ ] 5/5 Xecurify policies have `catalog_profile`
- [ ] `POST /tools/search_policy_catalog` returns IR doc for `"security incident notification"`
- [ ] pytest green for R0 tests

---

### R0 files touched (summary)

| File | Change |
|------|--------|
| `schemas/policy_catalog.py` | **new** |
| `prompts/policy_profiler.md` | **new** |
| `services/policy_profiler.py` | **new** |
| `services/catalog_search.py` | **new** |
| `services/ingest.py` | +8 lines hook |
| `store/pgvector_store.py` | +upsert + search catalog |
| `config.py`, `.env.example` | +4 settings |
| `migrations/007_*.sql` | **new** |
| `mcp/document_server/main.py` | +1 route |
| `document_client.py` (×2) | +1 method |
| `tests/test_policy_profiler.py`, `test_catalog_search.py` | **new** |

**Do NOT touch in R0:** `review_graph.py`, `multi_retrieval.py`, `named_policy_routing.py`.

---

## R1 — Obligation extraction (scaffold)

### Goal

Parse each contract section into **obligations** with boilerplate flags. Store in `ReviewState`. **Flag off = no-op** — existing section pipeline unchanged.

### R1 data model

**`ContractObligation`** (`review_agent/schemas/obligation.py`):

```python
class ContractObligation(BaseModel):
    obligation_id: str          # "{section_id}-o{index}"
    section_id: str
    text: str                   # obligation span (substring of section.text)
    char_start: int = 0         # offset in section.text
    char_end: int = 0
    obligation_type: str = "" # free text: incident_notification, governing_law, ...
    is_boilerplate: bool = False
    explicit_policy_mentions: list[str] = Field(default_factory=list)
    extract_source: Literal["llm", "fallback", "lexical"] = "fallback"
```

**`ReviewState` additions** (`review_state.py`):

```python
obligations: list[dict[str, Any]]           # serialized ContractObligation
obligation_extract_stats: dict[str, Any]   # counts, warnings
```

No `obligation_routing_by_id` until R2.

---

### R1 task breakdown

#### R1.1 — Schema (~50 LOC)

| # | Task | File |
|---|------|------|
| 1 | `ContractObligation`, `ObligationExtractResult` | `schemas/obligation.py` |
| 2 | Add fields to `ReviewState` | `state/review_state.py` |
| 3 | Config flags | `config.py` |

```env
OBLIGATION_ROUTING_ENABLED=false    # master switch — R1 ships false
OBLIGATION_EXTRACT_ENABLED=true     # run extract node even when routing off (populate state for tests)
OBLIGATION_EXTRACT_BATCH_SIZE=3
OBLIGATION_EXTRACT_MAX_SECTION_CHARS=8000
```

When `OBLIGATION_ROUTING_ENABLED=false`, downstream nodes ignore `obligations` (R2+ concern). R1 only populates state + artifact stats.

---

#### R1.2 — Boilerplate detection (~60 LOC)

**Reuse** `section_gap_status.py` patterns — obligation-level, not new regex zoo.

| # | Task | File |
|---|------|------|
| 1 | `infer_obligation_boilerplate(text, section_title) -> bool` | `services/obligation_boilerplate.py` |
| 2 | Map title patterns: notices, counterparts, severability, entire agreement, governing law | reuse `_BOILERPLATE_TITLE`, `_GOVERNING_LAW_TITLE` |
| 3 | Post-LLM override: if section title is boilerplate → mark all obligations `is_boilerplate=True` | |

**Universal boilerplate types** (config list, default):

```python
BOILERPLATE_OBLIGATION_TYPES = frozenset({
    "governing_law", "notices", "counterparts", "severability",
    "entire_agreement", "assignment", "signatures", "boilerplate",
})
```

If LLM returns `obligation_type` in this set → `is_boilerplate=True`.

---

#### R1.3 — Extraction LLM (~130 LOC)

| # | Task | File |
|---|------|------|
| 1 | Prompt `obligation_extract.md` | `prompts/obligation_extract.md` |
| 2 | `extract_obligations_for_section(section) -> list[ContractObligation]` | `services/obligation_extract.py` |
| 3 | `extract_obligations_batch(sections, settings)` — batch N sections/call | same |
| 4 | **Fallback:** 1 obligation = full `section.text`, `extract_source=fallback` | |
| 5 | **Explicit mentions:** regex-lite scan per obligation text (reuse `_POLICY_REF_RE` from `named_policy_routing.py` — import, don't duplicate) | |

**LLM output per section:**

```json
{
  "section_id": "2.3",
  "obligations": [
    {
      "index": 0,
      "text": "Receiving Party shall implement security measures...",
      "obligation_type": "security_controls",
      "explicit_policy_mentions": ["Security Practices Policy"]
    }
  ]
}
```

Post-process: assign `obligation_id`, compute `char_start`/`char_end` via `section.text.find(obligation.text)` (fallback: 0, len).

---

#### R1.4 — Graph node (~45 LOC)

| # | Task | File |
|---|------|------|
| 1 | `obligation_extract_node(state, client)` | `graph/obligation_nodes.py` **new** |
| 2 | Input: `contract_sections` from `filter_review_sections` | reuse `section_filter.py` |
| 3 | If `not settings.obligation_extract_enabled`: return `{}` | |
| 4 | Wire in `review_graph.py` **after** `clause_detection`, **before** `contract_routing` | |

```python
graph.add_edge("clause_detection", "obligation_extract")
graph.add_edge("obligation_extract", "contract_routing")
```

When flag off, node still runs if `OBLIGATION_EXTRACT_ENABLED=true` (for dev/eval); set both false to zero cost.

**Do NOT** branch retrieval/compare in R1.

---

#### R1.5 — Artifact stats (~25 LOC)

In `review_artifact.py` / `compliance_stats`:

```json
{
  "obligation_count": 42,
  "boilerplate_obligation_count": 8,
  "obligations_per_section_avg": 1.4
}
```

No per-obligation audit blob until R7.

---

#### R1.6 — Tests (~150 LOC)

| Test | Assert |
|------|--------|
| `test_obligation_fallback_one_per_section` | LLM mock fails → 1 obligation per section |
| `test_obligation_mixed_section` | §2.3 fixture → ≥2 obligations |
| `test_obligation_boilerplate_notices` | §10.5 → `is_boilerplate=True` |
| `test_obligation_boilerplate_governing_law` | §10.1 → boilerplate, type `governing_law` |
| `test_obligation_explicit_mention` | Text with "Security Practices Policy" → mention captured |
| `test_graph_node_flag_off` | `OBLIGATION_EXTRACT_ENABLED=false` → node returns `{}` |
| `test_review_state_obligations` | E2E graph invoke → `obligations` populated |

**Golden fixtures:** `tests/fixtures/xecurify_obligation_sections.json` — 5 section IDs (2.3, 10.1, 10.5, 3.2, 5.2) with expected boilerplate flags.

---

#### R1.7 — Ops

No MCP restart required for R1 (review_agent only). Run:

```bash
cd review/review_agent && pytest tests/test_obligation_*.py -q
```

Optional: run full Xecurify review with `OBLIGATION_EXTRACT_ENABLED=true` — pipeline output unchanged, artifact shows obligation stats.

**R1 done when:**

- [ ] Obligations in state for Xecurify review (flag on)
- [ ] §10.1, §10.5 marked boilerplate
- [ ] §2.3 yields multiple obligations
- [ ] `OBLIGATION_ROUTING_ENABLED=false` — findings identical to pre-R1 baseline

---

### R1 files touched (summary)

| File | Change |
|------|--------|
| `schemas/obligation.py` | **new** |
| `services/obligation_extract.py` | **new** |
| `services/obligation_boilerplate.py` | **new** |
| `prompts/obligation_extract.md` | **new** |
| `graph/obligation_nodes.py` | **new** |
| `graph/review_graph.py` | +1 node, +2 edges |
| `state/review_state.py` | +2 fields |
| `config.py` | +3 settings |
| `services/review_artifact.py` | +stats block |
| `tests/test_obligation_*.py`, fixtures | **new** |

**Do NOT touch in R1:** `multi_retrieval.py`, `section_compare_nodes.py`, `policy_discovery.py`, `named_policy_routing.py`.

---

## Execution order (day-by-day)

### R0

| Day | Tasks |
|-----|-------|
| 1 | R0.1 schema + R0.2 prompt + profiler service (keyword path) |
| 2 | R0.2 LLM path + R0.3 ingest hook |
| 3 | R0.4 migration + catalog vector upsert |
| 4 | R0.5 catalog search service + MCP route + clients |
| 5 | R0.6 tests + R0.7 ops backfill Xecurify |

### R1

| Day | Tasks |
|-----|-------|
| 1 | R1.1 schema + R1.2 boilerplate |
| 2 | R1.3 extraction LLM + fallback |
| 3 | R1.4 graph node + R1.5 artifact stats |
| 4 | R1.6 tests + golden fixtures |
| 5 | Buffer: Xecurify eval, fix edge cases |

---

## Interface contract (R0 → R2)

R2 **Semantic Routing Planner** will consume:

```python
# From R0 (via list_policy_registry or search_policy_catalog)
catalog_profiles: list[PolicyCatalogProfile]
document_id_by_ref: dict[str, UUID]

# From R1
obligations: list[ContractObligation]  # non-boilerplate only for routing
```

R0 `search_policy_catalog(query, tenant_id, top_k=10)` is the first routing primitive R3 catalog matcher will call.

---

## Risk mitigations

| Risk | Mitigation |
|------|------------|
| Skip-reindex skips profiler | Profile update on skip path (R0.3) |
| LLM profiler 429 at ingest | Keyword fallback; retry in sync_service |
| Obligation char offsets wrong | Grounding still uses section_id; offsets for audit only in R1 |
| Extra LLM cost per review | `OBLIGATION_EXTRACT_ENABLED=false` in prod until R2 ready |
| Two metadata systems | R0 `topics[]` free-form; section `categories[]` unchanged — catalog search uses profile only |

---

## Out of scope (explicit)

- Semantic routing planner (R2)
- Catalog matcher wiring (R3)
- Changing retrieval/compare graph path
- Policy graph / relationships
- Removing `named_policy_routing.py`
- Tenant admin UI for aliases

---

## Immediate first PR (R0.1–R0.3 only)

Smallest mergeable slice:

1. `PolicyCatalogProfile` schema  
2. `policy_profiler.py` + prompt (keyword fallback)  
3. Ingest hook writing `metadata.catalog_profile`  
4. One unit test  

Deploy + backfill. Vectors + search in PR 2.
