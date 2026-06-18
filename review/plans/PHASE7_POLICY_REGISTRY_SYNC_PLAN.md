# Phase 7 — Policy Registry, Lookup & Sync (Python)

**Plan ID:** `DR-PHASE-7`  
**Status:** Superseded  
**Use instead:** [PHASE7_JAVA_CATALOG_INTEGRATION_PLAN.md](./PHASE7_JAVA_CATALOG_INTEGRATION_PLAN.md) + [JAVA_CATALOG_API_CONTRACT.md](./JAVA_CATALOG_API_CONTRACT.md)

> v1 of this doc included Python Drive connectors. **v2 locks:** Java connects all sources; Python stays source-agnostic.

**Prerequisite:** Phase 4 pgvector core, Phase 6 contract-first discovery  
**Principle:** Extend `document_core` + document-mcp — **no review graph rewrite**

---

## 1. Executive summary

Today policies enter the system only via **`index_policy` with full text** or inline `policies[]` on review. Missing pieces:

| Gap | Phase 7 deliverable |
|-----|---------------------|
| Catalog registers metadata before text is indexed | `register_policy` MCP tool |
| Resolve `policy_ref` → `document_id` without HTTP catalog | `get_policy_by_ref` MCP tool |
| Batch sync from external catalog | `sync_policy_from_catalog` MCP tool (Python) |
| Docker/prod still uses in-memory store | document-mcp + compose **pgvector defaults** |

**Estimated new code:** ~400–550 lines across `document_core`, document-mcp, clients, tests.

---

## 2. Current state (verified)

| Exists | Location |
|--------|----------|
| `index_policy` | `document_server/main.py` L115 |
| `list_policies` (IDs only) | `main.py` L157 |
| `policy_documents` table | `migrations/001_document_corpus.sql` |
| `PgVectorDocumentStore.save_document` | Writes registry + chunks on full ingest |
| `HttpPolicyCatalogClient` | `review_agent/clients/policy_catalog.py` (review-only) |
| Prod env template | `review_agent/.env.production.example` |

| Missing | Impact |
|---------|--------|
| `register_policy` | Cannot register ref/title before blob indexed |
| `get_policy_by_ref` | Gap pass + discovery must search by topic only |
| Sync tool | Ops must manually call `index_policy` |
| compose `document-mcp` env | Container defaults to **memory** (no `DATABASE_URL`) |

**Schema constraint:** `policy_documents.content_hash NOT NULL` — metadata-only rows need migration `002`.

---

## 3. Target flows

### 3.1 Register then index (catalog / Java)

```text
Java / admin
  → POST /tools/register_policy     { tenant_id, policy_ref, title, ... }   # no text
  → POST /tools/sync_policy_from_catalog { tenant_id, policy_ref }          # fetch text + index
  → policy_documents: index_status=indexed, chunks in document_chunks

User
  → POST /query { contract_text }   # tenant_auto discovers indexed policies
```

### 3.2 Lookup by ref (review / gap pass)

```text
policy_ref known
  → get_policy_by_ref(tenant, ref) → document_id + title + index_status
  → if indexed: get_section / search scoped to document_id
```

---

## 4. Configuration (minimal)

### 4.1 New env vars (document-mcp + document_core)

| Env | Default (dev) | Prod (compose) |
|-----|---------------|----------------|
| `DOCUMENT_STORE_BACKEND` | `memory` | `pgvector` |
| `DATABASE_URL` | — | `postgresql://...` |
| `POLICY_CATALOG_URL` | — | `http://catalog:9000/api/v1` |
| `POLICY_SYNC_ENABLED` | `true` | `true` |

**Do not** change `document_core` code default from `memory` — only **docker / `.env.production`**.

### 4.2 Stable `document_id` for refs

Reuse existing helper (review agent):

```text
uuid5(NAMESPACE_DNS, f"{tenant_id}:{policy_ref}")
```

`register_policy` and `sync_policy_from_catalog` must use the **same** ID so re-sync is idempotent.

---

## 5. Database migration `002`

**File:** `document_core/migrations/002_policy_registry_status.sql`

```sql
ALTER TABLE policy_documents
  ADD COLUMN IF NOT EXISTS index_status TEXT NOT NULL DEFAULT 'indexed'
    CHECK (index_status IN ('pending', 'indexed', 'failed'));

ALTER TABLE policy_documents
  ALTER COLUMN content_hash DROP NOT NULL;

-- Backfill existing rows
UPDATE policy_documents SET index_status = 'indexed' WHERE content_hash IS NOT NULL;
```

| `index_status` | Meaning |
|----------------|---------|
| `pending` | `register_policy` only — no chunks |
| `indexed` | `index_policy` / sync completed |
| `failed` | Last sync error (store message in `metadata`) |

**Acceptance:** Migration applies on document-mcp startup (`run_migrations`).

---

## 6. Sprint 7A — `register_policy` (metadata-only)

### 7A.1 Schema

**File (new):** `document_core/schemas/registry.py`

```python
class RegisterPolicyRequest(BaseModel):
    tenant_id: str
    policy_ref: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    document_id: UUID | None = None          # optional; default uuid5(tenant, ref)
    policy_type: str | None = None
    applies_to_contract_types: list[str] = Field(default_factory=list)
    source: str = "catalog"
    metadata: dict[str, Any] = Field(default_factory=dict)

class PolicyRegistryRecord(BaseModel):
    tenant_id: str
    document_id: UUID
    policy_ref: str
    title: str
    policy_type: str | None
    applies_to_contract_types: list[str]
    index_status: Literal["pending", "indexed", "failed"]
    content_hash: str | None
    source: str
    metadata: dict[str, Any]
    indexed_at: datetime | None = None
```

### 7A.2 Service

**File (new):** `document_core/services/registry.py`

```python
def stable_policy_document_id(tenant_id: str, policy_ref: str) -> UUID: ...

async def register_policy(request: RegisterPolicyRequest, *, store=None) -> PolicyRegistryRecord:
    """Upsert policy_documents row with index_status=pending, no chunks."""
```

**Rules:**

- Upsert on `(tenant_id, policy_ref)` via unique index `ix_policy_documents_ref`
- `content_hash = NULL`, `index_status = pending`
- Do **not** delete existing chunks if re-registering an **indexed** doc (no-op or metadata-only update — configurable flag `allow_register_overwrite=false` default)

### 7A.3 Store methods

**Files:** `pgvector_store.py`, `memory_store.py`

| Method | pgvector | memory |
|--------|----------|--------|
| `upsert_policy_registry(record)` | SQL upsert | dict `_registry` |
| `get_policy_by_ref(tenant, ref)` | SELECT | dict lookup |

Extend `DocumentStore` **Protocol** optionally; memory/pgvector implement. Callers use `get_store()` + `hasattr` fallback for tests.

### 7A.4 MCP tool

**File:** `mcp/document_server/main.py`

```python
@app.post("/tools/register_policy", response_model=PolicyRegistryRecord)
async def register_policy_tool(request: RegisterPolicyRequest) -> PolicyRegistryRecord:
    return await register_policy(request)
```

### 7A.5 Clients

| File | Change |
|------|--------|
| `review_agent/clients/document_client.py` | `register_policy()` |
| `legal_ai_platform/mcp/document_client.py` | same |

### 7A.6 Tests

| ID | Test |
|----|------|
| T7A.1 | Register without text → `index_status=pending`, `list_sections` empty |
| T7A.2 | Re-register same ref updates title, keeps `document_id` |
| T7A.3 | pgvector + memory parity |

**Lines:** ~120

---

## 7. Sprint 7B — `get_policy_by_ref`

### 7B.1 Service

**File:** `document_core/services/registry.py`

```python
async def get_policy_by_ref(
    tenant_id: str,
    policy_ref: str,
    *,
    store=None,
) -> PolicyRegistryRecord | None:
```

Return full registry row (not chunks). 404 → `None` at MCP layer → HTTP 404.

### 7B.2 MCP tool

```python
class GetPolicyByRefRequest(BaseModel):
    tenant_id: str
    policy_ref: str

@app.post("/tools/get_policy_by_ref", response_model=PolicyRegistryRecord)
async def get_policy_by_ref_tool(request: GetPolicyByRefRequest) -> PolicyRegistryRecord:
    ...
```

### 7B.3 Review agent integration (minimal)

| File | Change |
|------|--------|
| `policy_discovery.py` | Optional enrich: if `policy_ref` in metadata, skip |
| `policy_retrieval.py` | Gap pass: if `policy_ref_by_doc` miss, try `get_policy_by_ref` before catalog HTTP |
| `gap_retrieval.py` | Use `document_id` from registry when `index_status=indexed` |

**Do not** remove `HttpPolicyCatalogClient` — use registry first, catalog fetch as fallback.

### 7B.4 `list_policies` enhancement (optional, small)

Return `{document_id, title, policy_ref, index_status}[]` instead of IDs only.

**New tool** `list_policy_registry` to avoid breaking `list_policies` contract.

```python
@app.post("/tools/list_policy_registry")
```

### 7B.5 Tests

| ID | Test |
|----|------|
| T7B.1 | Register → get_by_ref returns pending |
| T7B.2 | Index → get_by_ref returns indexed + same document_id |
| T7B.3 | Unknown ref → 404 |

**Lines:** ~100

---

## 8. Sprint 7C — Python sync service (catalog → index)

**Not Java.** One MCP tool that **orchestrates** existing pieces.

### 7C.1 Catalog fetch in document_core (thin, no review import)

**File (new):** `document_core/services/catalog_fetch.py`

```python
async def fetch_policy_text(
    catalog_url: str,
    tenant_id: str,
    policy_ref: str,
) -> tuple[str, str, dict]:  # title, text, metadata
```

Mirror `HttpPolicyCatalogClient` JSON shape — **no dependency on review_agent**.

Env: `POLICY_CATALOG_URL` on document-mcp.

### 7C.2 `sync_policy_from_catalog` tool

**File:** `document_core/services/sync.py`

```python
class SyncPolicyRequest(BaseModel):
    tenant_id: str
    policy_ref: str
    force_reindex: bool = False

async def sync_policy_from_catalog(request: SyncPolicyRequest) -> IngestResult:
```

**Steps:**

1. `register_policy` if no registry row (title from catalog response)
2. `fetch_policy_text` from `POLICY_CATALOG_URL`
3. `ingest_document` / `index_policy` with `document_id=stable_id`, `metadata.policy_ref=ref`
4. Set `index_status=indexed` on success; `failed` + error in metadata on failure

**MCP:** `POST /tools/sync_policy_from_catalog`

### 7C.3 Batch sync (optional, same sprint)

```python
class SyncPoliciesBatchRequest(BaseModel):
    tenant_id: str
    policy_refs: list[str]
    force_reindex: bool = False

@app.post("/tools/sync_policies_batch")
```

Sequential with concurrency limit 3 — avoid catalog overload.

### 7C.4 Admin / ops entry points (pick one)

| Option | Effort | Recommendation |
|--------|--------|----------------|
| MCP tools only | Low | **v1** — curl / CI job calls document-mcp |
| `legal_ai_platform` `POST /admin/policies/sync` | Medium | v1.1 — proxies to document-mcp |
| Celery worker | High | Defer — reuse crawler-worker pattern in Phase 8 |

**Java handoff (interface only, document in plan):**

```http
PUT  /api/v1/tenants/{t}/policies/{ref}     → calls register_policy
POST /api/v1/tenants/{t}/policies/{ref}/sync → calls sync_policy_from_catalog
```

### 7C.5 Tests

| ID | Test |
|----|------|
| T7C.1 | Mock catalog HTTP → sync → search_policy finds doc |
| T7C.2 | Sync idempotent (same content_hash skip re-embed) |
| T7C.3 | Catalog 404 → `index_status=failed` |

**Lines:** ~150

---

## 9. Sprint 7D — Production defaults (pgvector)

### 7D.1 Docker compose

**File:** `Legal ai/docker-compose.yml` — `document-mcp` service:

```yaml
environment:
  LOG_LEVEL: INFO
  DOCUMENT_STORE_BACKEND: pgvector
  DATABASE_URL: postgresql://legalai:legalai@postgres:5432/legalai
  SEARCH_BACKEND: hybrid
depends_on:
  postgres:
    condition: service_healthy
```

Add `postgres` to `document-mcp` networks if missing.

### 7D.2 Env examples

| File | Add |
|------|-----|
| `Legal ai/.env.example` | `DOCUMENT_STORE_BACKEND=pgvector` comment block for document-mcp |
| `document_core/.env.example` (new if missing) | `DATABASE_URL`, `DOCUMENT_STORE_BACKEND` |
| `review_agent/.env.production.example` | Cross-ref document-mcp URL |

### 7D.3 Health check

**File:** `document_server/main.py` `/health`

```json
{ "status": "ok", "store_backend": "pgvector", "db": "ok" }
```

Ping DB when pgvector; fail health if DB down.

### 7D.4 Dev unchanged

- Local pytest: `InMemoryDocumentStore` (no `DATABASE_URL`)
- `get_settings()` defaults stay `memory`

### 7D.5 Acceptance

| Check | Expected |
|-------|----------|
| `docker compose up document-mcp` | Uses pgvector, migrations run |
| `pytest review_agent/tests` | Still pass with memory (isolated_store) |
| Contract-only review against compose stack | Discovers pre-synced policies |

**Lines:** ~40 (config only)

---

## 10. `index_policy` integration tweak

**File:** `pgvector_store.py` `save_document`

After successful index:

```sql
UPDATE policy_documents SET index_status = 'indexed' WHERE tenant_id = ? AND document_id = ?
```

If row missing but `metadata.policy_ref` set → upsert registry row as indexed.

**File:** `ingest.py` — pass `metadata.document_title` (already done in 6B).

**Lines:** ~15

---

## 11. Implementation order

```text
[ ] 7D.1–7D.3  Prod compose + health (can ship first for infra)
[ ] 002 migration + index_status
[ ] 7A register_policy (service + store + MCP + client)
[ ] 7B get_policy_by_ref + list_policy_registry
[ ] 7C catalog_fetch + sync_policy_from_catalog (+ batch)
[ ] 7B.3 review gap_retrieval hook (optional small)
[ ] Tests T7A–T7C
```

**Dependency graph:**

```text
002 migration → 7A → 7B → 7C → review hooks
7D parallel (compose)
```

---

## 12. Task checklist (precise)

| ID | Task | File(s) | Est. lines |
|----|------|---------|------------|
| 7.0.1 | Migration `002_policy_registry_status.sql` | `document_core/migrations/` | 15 |
| 7.0.2 | `schemas/registry.py` | document_core | 45 |
| 7A.1 | `services/registry.py` register + stable UUID | document_core | 60 |
| 7A.2 | `upsert_policy_registry` pgvector + memory | stores | 80 |
| 7A.3 | MCP `register_policy` | document_server/main.py | 15 |
| 7A.4 | Client methods | document_client ×2 | 20 |
| 7B.1 | `get_policy_by_ref` service + store | document_core | 40 |
| 7B.2 | MCP `get_policy_by_ref` | main.py | 15 |
| 7B.3 | MCP `list_policy_registry` | main.py | 20 |
| 7B.4 | `gap_retrieval` registry lookup | review_agent | 25 |
| 7C.1 | `catalog_fetch.py` | document_core | 50 |
| 7C.2 | `sync.py` + MCP tools | document_core + main.py | 90 |
| 7D.1 | docker-compose document-mcp env | Legal ai | 15 |
| 7D.2 | Health store backend | main.py | 20 |
| 7.3.1 | `index_status` update on save_document | pgvector_store.py | 15 |
| 7T | Tests registry + sync | document_core/tests + review_agent/tests | 120 |

**Total:** ~530 lines

---

## 13. What we do NOT build in Phase 7

| Item | Phase |
|------|-------|
| Java catalog service | Phase 8 |
| Google Drive OAuth | Phase 8 |
| Delete/tombstone policy MCP | Phase 8 |
| Celery scheduled sync worker | Phase 8 |
| Change review graph nodes | No |
| Touch `deep_research` agent | No |

---

## 14. Acceptance criteria (Phase 7 done)

- [ ] `register_policy` creates pending registry row without chunks  
- [ ] `get_policy_by_ref` returns `document_id` + `index_status`  
- [ ] `sync_policy_from_catalog` fetches catalog + indexes into pgvector  
- [ ] `docker compose` document-mcp uses pgvector by default  
- [ ] Contract-only review finds synced policies after sync  
- [ ] Dev/pytest still uses memory — no regression (52+ tests)  
- [ ] Review agent unchanged for `policies[]` / `policy_refs[]` request paths  

---

## 15. Risk mitigations

| Risk | Mitigation |
|------|------------|
| Breaking `list_policies` | New `list_policy_registry`; keep old tool |
| Duplicate index on sync | Existing `content_hash` skip in `save_document` |
| Catalog down | `index_status=failed`, review gets warning not crash |
| memory vs pgvector drift | Same `registry.py` service API both stores |

---

*Document version: 1.0 — policy registry, lookup, Python sync, prod pgvector defaults.*
