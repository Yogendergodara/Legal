# Phase 4 — Persistent RAG Store (pgvector + Tenant Policy Index)

**Plan ID:** `DR-PHASE-4`  
**Status:** Ready to plan — not started  
**Prerequisite:** Phase 1–3 review agent (done); orchestrator policy scope fixes (#1–#2) recommended first  
**Primary stack:** **PostgreSQL + pgvector** (reuse `Legal ai` infra)  
**Optional later:** Qdrant backend behind same `DocumentStore` interface  

---

## 1. Executive summary

Replace `InMemoryDocumentStore` in `document_core` with a **persistent, tenant-scoped vector index** for contract + policy chunks. Add a **tenant policy registry** (metadata catalog) separate from chunk vectors so Java/Drive sync can register policies without re-parsing on every review.

**Principle:** Extend `DocumentStore` protocol — do **not** rewrite `review_agent` graph. Swap store + search at `document-mcp` boundary.

---

## 2. Root cause (why we need this)

| Symptom | Cause today |
|---------|-------------|
| Policies lost on restart | `InMemoryDocumentStore` in RAM |
| Multi-instance gateway breaks | No shared store between workers |
| `list_policies()` returns stale tenant docs | No catalog lifecycle / versioning |
| Poor recall on paraphrases | Lexical BM25 only (`search/lexical.py`) |
| Review scans all tenant policies | No scoped registry + index filters |
| Java catalog not integrated | No `policy_documents` table |

---

## 3. Architecture decision

### 3.1 pgvector vs Qdrant

| Criterion | pgvector | Qdrant |
|-----------|----------|--------|
| Already in repo | Yes (`Legal ai/docker-compose.yml`, `db/search.py`) | No |
| Hybrid FTS + vector | Native Postgres `tsvector` + pgvector | Needs separate text index |
| Tenant isolation | Row-level `tenant_id` + RLS | Collection per tenant or payload filter |
| Ops complexity | One DB | Extra service |
| **Verdict** | **Primary for v1** | Optional `DocumentStore` impl later |

### 3.2 Two layers (do not merge)

```text
┌─────────────────────────────────────┐
│  policy_documents (registry)        │  ← catalog metadata, Java sync
│  tenant_id, document_id, policy_ref │
│  policy_type, applies_to, version   │
└──────────────┬──────────────────────┘
               │ document_id FK
┌──────────────▼──────────────────────┐
│  document_chunks (RAG index)          │  ← parent/child + embedding + tsv
│  tenant_id, document_id, chunk_role │
│  section_id, parent_id, text, ...   │
└─────────────────────────────────────┘
```

- **Registry** answers: *which policies exist for tenant?* *which apply to MSA?*
- **Chunks** answers: *which section matches query?*

Review agent continues: `list_policies` → registry; `search_policy` → chunks.

---

## 4. Target data model (SQL)

**File (new):** `document_core/migrations/001_document_corpus.sql`

### 4.1 `policy_documents` (tenant policy registry)

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE policy_documents (
    tenant_id           TEXT NOT NULL,
    document_id         UUID NOT NULL,
    kind                TEXT NOT NULL CHECK (kind IN ('policy', 'contract')),
    title               TEXT NOT NULL,
    policy_type         TEXT,
    applies_to_contract_types TEXT[] DEFAULT '{}',
    policy_ref          TEXT,                    -- external catalog id (Drive, Java)
    content_hash        TEXT NOT NULL,           -- skip re-embed if unchanged
    effective_date      DATE,
    version_label       TEXT,
    source              TEXT DEFAULT 'upload',   -- upload | catalog | sync
    metadata            JSONB DEFAULT '{}',
    indexed_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, document_id)
);

CREATE UNIQUE INDEX ix_policy_documents_ref
    ON policy_documents (tenant_id, policy_ref)
    WHERE policy_ref IS NOT NULL;

CREATE INDEX ix_policy_documents_tenant_kind
    ON policy_documents (tenant_id, kind);

CREATE INDEX ix_policy_documents_applies
    ON policy_documents USING GIN (applies_to_contract_types);
```

### 4.2 `document_chunks` (parent–child RAG)

```sql
-- EMBEDDING_DIM = 384 to match Legal ai retrieval_server (all-MiniLM-L6-v2)
CREATE TABLE document_chunks (
    id                  BIGSERIAL PRIMARY KEY,
    tenant_id           TEXT NOT NULL,
    document_id         UUID NOT NULL,
    chunk_id            TEXT NOT NULL,
    chunk_role          TEXT NOT NULL CHECK (chunk_role IN ('parent', 'child')),
    parent_id           TEXT,
    section_id          TEXT NOT NULL,
    section_path        TEXT,
    title               TEXT,
    text                TEXT NOT NULL,
    context_text        TEXT,
    kind                TEXT NOT NULL,
    policy_type         TEXT,
    applies_to_contract_types TEXT[] DEFAULT '{}',
    embedding           vector(384),
    tsv                 tsvector,
    metadata            JSONB DEFAULT '{}',
    UNIQUE (tenant_id, document_id, chunk_id)
);

CREATE INDEX ix_document_chunks_parents
    ON document_chunks (tenant_id, document_id, chunk_role)
    WHERE chunk_role = 'parent';

CREATE INDEX ix_document_chunks_children_doc
    ON document_chunks (tenant_id, document_id, chunk_role)
    WHERE chunk_role = 'child';

CREATE INDEX ix_document_chunks_tsv
    ON document_chunks USING GIN (tsv);

CREATE INDEX ix_document_chunks_embedding
    ON document_chunks USING hnsw (embedding vector_cosine_ops);

CREATE INDEX ix_document_chunks_tenant_kind
    ON document_chunks (tenant_id, kind);
```

### 4.3 `document_canonical` (optional, or store in registry)

```sql
CREATE TABLE document_canonical (
    tenant_id       TEXT NOT NULL,
    document_id     UUID NOT NULL,
    canonical_text  TEXT NOT NULL,
    PRIMARY KEY (tenant_id, document_id)
);
```

---

## 5. Code changes (minimal surface)

### 5.1 Extend `DocumentStore` protocol

**File:** `document_core/store/memory_store.py` → rename module to `protocol.py` or keep and add:

```python
class DocumentStore(Protocol):
    # existing methods unchanged
    async def search_children(  # new — used by search.py
        self,
        *,
        tenant_id: str,
        query: str,
        query_vector: list[float] | None,
        kind: DocumentKind | None,
        document_ids: list[UUID] | None,
        policy_type: str | None,
        contract_type: str | None,
        top_k: int,
    ) -> list[tuple[float, IndexedChunk]]: ...
```

**Alternative (smaller diff):** Keep protocol sync; add `PgVectorDocumentStore` implementing existing methods + internal hybrid search used only from `search.py` when `DOCUMENT_STORE_BACKEND=pgvector`.

### 5.2 New store implementation

**File (new):** `document_core/store/pgvector_store.py` (~250 lines)

| Method | Implementation |
|--------|----------------|
| `save_document` | Upsert registry row + bulk upsert chunks + embed children async |
| `get_parents` / `get_children` | `SELECT` by `(tenant_id, document_id, chunk_role)` |
| `get_parent_by_section` | `section_id` index lookup |
| `list_documents` | `SELECT document_id FROM policy_documents WHERE tenant_id AND kind` |
| `get_canonical_text` | `document_canonical` table |
| `delete_document` | Cascade delete chunks + registry (for re-index) |

**Idempotent ingest:** Compare `content_hash`; if unchanged, skip re-embed.

### 5.3 Embedding service (shared)

**File (new):** `document_core/embeddings/service.py` (~60 lines)

Reuse pattern from `Legal ai/mcp/retrieval_server/embedding_service.py`:

- Model: `sentence-transformers/all-MiniLM-L6-v2` (384-dim) — matches existing `EMBEDDING_DIM`
- Env: `EMBEDDING_MODEL`, `EMBEDDING_DEVICE=cpu`
- Batch embed on ingest (children only; parents resolved from children)

**Do not** import from `mcp.retrieval_server` directly (coupling). Copy minimal interface or extract to `legal_embeddings` shared package later.

### 5.4 Hybrid search

**File (new):** `document_core/search/hybrid.py` (~120 lines)

Port logic from `Legal ai/db/search.py`:

```text
score = alpha * fts_score + (1 - alpha) * cosine_similarity
```

- Default `alpha=0.5` (`SEARCH_HYBRID_ALPHA`)
- Search **children** only → dedupe to parents (existing `search.py` logic)
- Fallback: if no embedding service, use lexical only (dev mode)

**File:** `document_core/services/search.py` — branch:

```python
if settings.search_backend == "hybrid":
    scored = await hybrid_search_children(...)
else:
    scored = lexical_scan(...)  # current behavior
```

### 5.5 Store factory + config

**File (new):** `document_core/config.py`

```python
document_store_backend: Literal["memory", "pgvector"] = "memory"
database_url: str | None = None
search_backend: Literal["lexical", "hybrid"] = "lexical"
search_hybrid_alpha: float = 0.5
embedding_batch_size: int = 32
```

**File:** `document_core/store/__init__.py`

```python
def get_store() -> DocumentStore:
    if backend == "pgvector":
        return _pg_store_singleton
    return _memory_store
```

### 5.6 document-mcp wiring

**File:** `Legal ai/mcp/document_server/config.py`

```python
database_url: str = Field(default="postgresql://...")
document_store_backend: str = "pgvector"
```

**File:** `Legal ai/mcp/document_server/main.py`

- Lifespan: init pool, run migrations, `set_store(PgVectorDocumentStore(pool))`
- Health: check DB connectivity

### 5.7 Tenant policy index API (registry)

**New MCP tools (optional Phase 4b):**

| Tool | Purpose |
|------|---------|
| `POST /tools/list_policies` | Query registry (already exists — switch impl) |
| `POST /tools/register_policy` | Java catalog upsert metadata without full text |
| `POST /tools/delete_policy` | Tombstone policy + chunks |

**Review agent change (small):** `build_review_plan()` policy scope:

```python
# Only union list_policies when policy_scope == "tenant"
# Default policy_scope == "request" (indexed_policies + policy_document_ids + policy_refs only)
```

**File:** `review_agent/services/policy_plan.py` + config `review_policy_scope`.

### 5.8 Java backend handoff (later, interface now)

```http
PUT /api/v1/tenants/{tenant}/policies/{policy_ref}
Body: { document_id, title, policy_type, applies_to, content_hash, blob_url }

POST /api/v1/tenants/{tenant}/policies/{policy_ref}/index
→ triggers document-mcp index_policy from blob
```

Python document-mcp does **not** talk to Drive; Java sync + blob storage feeds `index_policy`.

---

## 6. Detailed subtasks

### Phase 4A — Foundation (Week 1)

| ID | Task | Files | Acceptance |
|----|------|-------|------------|
| 4A.1 | SQL migration `001_document_corpus.sql` | `document_core/migrations/` | `psql` applies clean |
| 4A.2 | `PgVectorDocumentStore.save_document` | `pgvector_store.py` | Ingest writes registry + chunks |
| 4A.3 | Read paths: parents, children, section, list | same | Existing tests pass against PG |
| 4A.4 | `asyncpg` or SQLAlchemy async pool | `document_core/db/pool.py` | Connection from `DATABASE_URL` |
| 4A.5 | Store factory + env config | `config.py`, `store/__init__.py` | `DOCUMENT_STORE_BACKEND=memory` default |
| 4A.6 | document-mcp lifespan + migration runner | `document_server/main.py` | Docker compose starts |

### Phase 4B — Embeddings + hybrid search (Week 2)

| ID | Task | Files | Acceptance |
|----|------|-------|------------|
| 4B.1 | Embedding service (384-dim) | `embeddings/service.py` | Batch embed 100 chunks < 5s CPU |
| 4B.2 | Embed on ingest (children) | `ingest.py` hook | Rows have non-null `embedding` |
| 4B.3 | `tsvector` update on ingest | SQL trigger or Python | FTS returns hits |
| 4B.4 | `hybrid.py` search | `search/hybrid.py` | Recall > lexical on paraphrase fixture |
| 4B.5 | Wire `search.py` hybrid branch | `services/search.py` | `SEARCH_BACKEND=hybrid` |
| 4B.6 | Golden recall tests | `tests/test_hybrid_search.py` | 5 query fixtures |

### Phase 4C — Tenant policy index + scope (Week 3)

| ID | Task | Files | Acceptance |
|----|------|-------|------------|
| 4C.1 | Registry CRUD in store | `pgvector_store.py` | `list_policies` from DB |
| 4C.2 | `review_policy_scope=request` default | `policy_plan.py`, `config.py` | Upload-only → only those docs reviewed |
| 4C.3 | `content_hash` skip re-embed | ingest | Same text → no embed call |
| 4C.4 | `delete_policy` tool | document-mcp | Chunks removed |
| 4C.5 | Orchestrator: `policy_refs` without `policies[]` | platform orchestrator | Gateway e2e |

### Phase 4D — Production hardening (Week 4)

| ID | Task | Files | Acceptance |
|----|------|-------|------------|
| 4D.1 | Row-level security (optional) | migration `002_rls.sql` | Tenant A cannot read B |
| 4D.2 | Connection pooling + timeouts | pool config | No leak under 50 concurrent |
| 4D.3 | Index maintenance job | script | `REINDEX` / vacuum schedule |
| 4D.4 | Observability: embed latency, search latency | document-mcp logs | p95 in logs |
| 4D.5 | Load test: 30 categories × hybrid search | k6 or pytest | < 200ms search p95 |

### Phase 4E — Qdrant (optional, defer)

| ID | Task | Notes |
|----|------|-------|
| 4E.1 | `QdrantDocumentStore` implements `DocumentStore` | Payload filters: `tenant_id`, `kind` |
| 4E.2 | Registry still in Postgres | Qdrant vectors only |
| 4E.3 | Env `DOCUMENT_STORE_BACKEND=qdrant` | For scale-out vector-only |

---

## 7. Dependencies to add

**`document_core/pyproject.toml`:**

```toml
[project.optional-dependencies]
pgvector = [
    "asyncpg>=0.29.0",
    "pgvector>=0.3.0",
    "sqlalchemy[asyncio]>=2.0.0",
]
embeddings = [
    "sentence-transformers>=3.0.0",
]
```

Keep embeddings optional for CI (lexical-only tests).

---

## 8. Test strategy

| Layer | Tests |
|-------|-------|
| Unit | `PgVectorDocumentStore` with testcontainers Postgres |
| Search | Hybrid vs lexical recall on `fixtures.py` contracts |
| Integration | document-mcp ingest → search → verify_quote |
| Review e2e | `test_review_e2e` against pgvector (marker `integration`) |
| CI default | `DOCUMENT_STORE_BACKEND=memory` (fast, no Docker) |
| CI nightly | `integration` job with `pgvector/pgvector:pg16` |

---

## 9. Rollout plan

```text
1. Deploy migration + pgvector store behind flag (memory default)
2. Staging: DOCUMENT_STORE_BACKEND=pgvector, SEARCH_BACKEND=lexical
3. Staging: SEARCH_BACKEND=hybrid
4. Enable review_policy_scope=request
5. Production: flip store backend per environment
6. Java catalog writes registry; Python indexes on demand
```

**Rollback:** Set `DOCUMENT_STORE_BACKEND=memory` — review agent unchanged.

---

## 10. What does NOT change

- `review_agent` graph nodes (except policy scope config)
- `ComplianceFinding` schemas
- MCP tool URLs (`/tools/ingest_document`, etc.)
- Parent–child chunk builder (`indexer/parent_child.py`)
- Grounding (`verify_quote` still substring on canonical text)

---

## 11. Risks and mitigations

| Risk | Mitigation |
|------|------------|
| Embedding model mismatch (384 vs 768) | Standardize on `all-MiniLM-L6-v2` = 384 |
| Re-index storm on every review | `content_hash` dedup |
| HNSW index build slow | Build after bulk ingest; use `CONCURRENTLY` |
| sentence-transformers heavy in API pod | Sidecar embed service or batch queue |
| Tenant data leak | `tenant_id` on every query + RLS |

---

## 12. Definition of done

- [ ] Policies survive document-mcp restart
- [ ] Two gateway workers share same index
- [ ] Hybrid search beats lexical on paraphrase golden set
- [ ] `list_policies` reads registry, scoped by tenant
- [ ] Default review uses request-scoped policies only
- [ ] CI passes with memory backend; integration job passes pgvector
- [ ] Migration documented in `document_core/README.md`

---

## 13. Estimated effort

| Phase | Production lines | Tests | Calendar |
|-------|-----------------|-------|----------|
| 4A Foundation | ~350 | ~150 | 1 week |
| 4B Hybrid search | ~200 | ~100 | 1 week |
| 4C Policy index + scope | ~120 | ~80 | 1 week |
| 4D Hardening | ~100 | ~50 | 1 week |
| **Total** | **~770** | **~380** | **4 weeks** |

Qdrant optional: +~300 lines, +1 week if needed at scale.

---

## 14. Recommended order vs other backlog

```text
Before Phase 4 (quick wins):
  → Orchestrator policy_refs fix (#1)
  → review_policy_scope=request (#2)  [can ship before pgvector using memory]

Phase 4A → 4B → 4C → 4D

After Phase 4:
  → Java catalog sync
  → PDF/DOCX ingest
  → Top-k hit merge in review agent
```
