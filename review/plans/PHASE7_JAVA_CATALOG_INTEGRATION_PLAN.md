# Phase 7 — Java Catalog Integration (Multi-Source, Source-Agnostic Python)

**Plan ID:** `DR-PHASE-7`  
**Status:** Implemented  
**Supersedes:** Drive-specific Python connectors (deferred to Java)  
**Prerequisite:** Phase 4 pgvector core, Phase 6 `tenant_auto` discovery  
**Principle:** **Java connects all sources** (Drive, Confluence, SharePoint, upload). **Python only indexes + reviews.**

---

## 1. Architecture decision (locked)

```text
┌──────────────────────────────────────────────────────────────────┐
│ JAVA — system of record                                          │
│  • OAuth + sync for Drive / Confluence / SharePoint / upload     │
│  • document_registry (Postgres): ref, id, source, version, hash  │
│  • Blob store (S3/MinIO): original PDF/DOCX                      │
│  • Catalog REST API (ONE API for all sources)                    │
│  • On sync: call document-mcp OR expose text for Python pull     │
└────────────────────────────┬─────────────────────────────────────┘
                             │
          ┌──────────────────┴──────────────────┐
          │                                     │
   SYNC (background)                    REVIEW (hot path)
          │                                     │
          ▼                                     ▼
  register_policy + index_policy          contract_text OR document_ids
  (document-mcp)                          LangGraph → search_policy
          │                                     │
          └──────────────► pgvector ◄───────────┘
```

| Layer | Owner | Python env needed? |
|-------|-------|-------------------|
| Source connectors | **Java** | No `GOOGLE_*`, no `CONFLUENCE_*` |
| Catalog metadata | **Java** Postgres | Optional `POLICY_CATALOG_URL` (pull sync only) |
| Vector index | **Python** document-mcp | `DOCUMENT_STORE_BACKEND`, `DATABASE_URL` |
| Review agent | **Python** | `REVIEW_POLICY_SOURCE=tenant_auto` |

**Python never calls Drive/Confluence at review time.**

---

## 2. Problem statement

| Gap today | Impact |
|-----------|--------|
| No `register_policy` | Cannot register metadata before text indexed |
| No `get_policy_by_ref` | Cannot resolve `drive:abc` → `document_id` without HTTP catalog |
| No `sync_policy_from_catalog` | Ops must manually POST `index_policy` with full text |
| docker `document-mcp` uses memory | Policies lost on container restart |
| `content_hash NOT NULL` | Cannot store metadata-only pending rows |

**Not a gap (already built):** `HttpPolicyCatalogClient`, `index_fetched_policy`, `stable_policy_document_id`, Phase 6 discovery, hybrid compliance.

---

## 3. Two sync patterns (both supported, pick one per deployment)

### Pattern A — Java push (recommended for prod)

Java sync job already downloaded text from any source:

```http
POST document-mcp/tools/register_policy
POST document-mcp/tools/index_policy
```

Python needs **no** `POLICY_CATALOG_URL` on document-mcp. Java owns fetch.

### Pattern B — Python pull (dev / thin Java)

Java catalog exposes text; Python worker pulls:

```http
POST document-mcp/tools/sync_policy_from_catalog
  { "tenant_id": "acme", "policy_ref": "drive:1a2b3c" }
```

Internally: `GET {POLICY_CATALOG_URL}/tenants/{t}/policies/{ref}` → `index_policy`.

**Same code path for Drive, Confluence, SharePoint** — only `policy_ref` prefix differs.

---

## 4. Review flows (unchanged graph, new data path)

### 4.1 Contract-only + tenant_auto (Phase 6 — primary prod path)

```text
User → POST /query { contract_text, tenant_id, task_type: review }
  → contract_routing → policy_discovery (search tenant pgvector)
  → hybrid compliance → report
```

**Requires:** policies pre-indexed by Java sync → document-mcp.

### 4.2 Explicit IDs (Java UI picks policies)

```json
{
  "tenant_id": "acme",
  "contract_document_id": "uuid-contract",
  "policy_document_ids": ["uuid-p1", "uuid-p2"],
  "task_type": "review"
}
```

### 4.3 Legacy refs (still supported)

```json
{
  "policy_refs": ["drive:abc", "confluence:page-99"]
}
```

Resolution order at gap/fetch time:

```text
1. get_policy_by_ref(tenant, ref)     ← NEW (document-mcp registry)
2. HttpPolicyCatalogClient.fetch      ← existing fallback
3. index_fetched_policy               ← existing
```

---

## 5. Java catalog API contract (implement in Java; Python consumes)

Base path: `/api/v1` (configurable via `POLICY_CATALOG_URL`).

### 5.1 Get policy (required)

```http
GET /api/v1/tenants/{tenant_id}/policies/{policy_ref}
```

`policy_ref` is URL-encoded opaque string, e.g. `drive%3A1a2b3c`, `confluence%3Apage-123`.

**200 response** (matches existing `PolicyDocument` in `policy_catalog.py`):

```json
{
  "title": "Vendor Management Policy",
  "text": "4. Limitation of Liability\n...",
  "policy_type": "vendor_policy",
  "applies_to_contract_types": ["msa", "vendor"],
  "document_id": "550e8400-e29b-41d4-a716-446655440000",
  "metadata": {
    "source": "google_drive",
    "drive_file_id": "1a2b3c",
    "folder_id": "xyz",
    "version": "7",
    "department": "Legal",
    "content_hash": "sha256...",
    "blob_uri": "s3://bucket/tenant/acme/policies/1a2b3c.pdf"
  }
}
```

**404** — policy not in catalog.

**Rules:**

| Field | Rule |
|-------|------|
| `document_id` | Stable UUID; if omitted Python computes `uuid5(tenant_id:policy_ref)` |
| `text` | Required for index; extracted plain text (not PDF bytes) |
| `metadata.source` | `google_drive` \| `confluence` \| `sharepoint` \| `upload` |
| `metadata.content_hash` | Java computes; Python skips re-embed if unchanged |

### 5.2 List policies (optional v1.1 — Java only)

```http
GET /api/v1/tenants/{tenant_id}/policies?kind=policy&status=INDEXED
```

Used by Java UI. Python discovery uses **vector search**, not this list.

### 5.3 Index webhook (optional — Java calls document-mcp directly)

Java does **not** need a Python webhook. Java calls document-mcp tools after sync.

---

## 6. Stable identifiers

```python
# Already in review_agent/clients/policy_catalog.py — reuse, do not duplicate
policy_ref = "drive:{file_id}" | "confluence:{page_id}" | "sharepoint:{item_id}"
document_id = uuid5(NAMESPACE_DNS, f"{tenant_id}:{policy_ref}")
```

Java **should** send `document_id` in catalog response. Python uses it for idempotent re-index.

---

## 7. Database migration `002`

**File:** `document_core/document_core/migrations/002_policy_registry_status.sql`

```sql
ALTER TABLE policy_documents
  ADD COLUMN IF NOT EXISTS index_status TEXT NOT NULL DEFAULT 'indexed'
    CHECK (index_status IN ('pending', 'indexed', 'failed'));

ALTER TABLE policy_documents
  ALTER COLUMN content_hash DROP NOT NULL;

UPDATE policy_documents
  SET index_status = 'indexed'
  WHERE content_hash IS NOT NULL;
```

| `index_status` | When |
|----------------|------|
| `pending` | `register_policy` — metadata only, no chunks |
| `indexed` | `index_policy` / sync completed |
| `failed` | Last sync error (message in `metadata.last_error`) |

---

## 8. Python implementation — minimal surface area

**Total new code target: ~420 lines** (excluding tests).  
**Reuse:** `policy_catalog.py`, `index_fetched_policy`, `ingest_document`, `HttpPolicyCatalogClient`.

### 8.1 New files (3)

| File | Lines | Purpose |
|------|------:|---------|
| `document_core/schemas/registry.py` | ~45 | `RegisterPolicyRequest`, `PolicyRegistryRecord`, sync request models |
| `document_core/services/registry.py` | ~90 | `register_policy`, `get_policy_by_ref`, `stable_policy_document_id` |
| `document_core/services/catalog_sync.py` | ~70 | `sync_policy_from_catalog` (fetch + index orchestration) |

`stable_policy_document_id` — **import from** `review_agent.clients.policy_catalog` is wrong (circular). **Move** helper to `document_core/services/registry.py` and re-export from `policy_catalog.py` (thin re-export, ~3 lines).

### 8.2 Modified files (8)

| File | Change | Lines |
|------|--------|------:|
| `pgvector_store.py` | `upsert_policy_registry`, `get_policy_by_ref`; set `index_status` on `save_document` | ~70 |
| `memory_store.py` | `_registry: dict` for pending rows + lookup | ~40 |
| `document_server/main.py` | 4 MCP routes + health backend field | ~55 |
| `review_agent/.../document_client.py` | 3 client methods | ~25 |
| `legal_ai_platform/.../document_client.py` | same 3 methods | ~25 |
| `policy_catalog.py` | import `stable_id` from document_core | ~5 |
| `policy_retrieval.py` | try `get_policy_by_ref` before catalog HTTP | ~20 |
| `docker-compose.yml` | document-mcp pgvector env | ~12 |

### 8.3 Files explicitly NOT changed

| File | Why |
|------|-----|
| `review_graph.py` | Flow already correct |
| `discovery_nodes.py` | Works once policies indexed |
| `orchestrator.py` | Already accepts refs + IDs |
| `deep_research_*` | Out of scope |
| New Drive/Confluence modules | Java owns connectors |

---

## 9. Sprint breakdown

### Sprint 7.0 — Migration + registry core (~2h)

| ID | Task | How |
|----|------|-----|
| 7.0.1 | Add migration `002` | Copy SQL from §7; `run_migrations` picks it up automatically |
| 7.0.2 | `schemas/registry.py` | Pydantic models only |
| 7.0.3 | Move `stable_policy_document_id` to `document_core/services/registry.py` | `policy_catalog.py` re-exports for backward compat |
| 7.0.4 | `register_policy()` service | Upsert `policy_documents` with `index_status=pending`, `content_hash=NULL`, no chunk writes |
| 7.0.5 | `get_policy_by_ref()` service | SELECT by `(tenant_id, policy_ref)` |

**Acceptance:** Unit test register → get returns pending, no sections.

---

### Sprint 7.1 — Store layer (~2h)

| ID | Task | How |
|----|------|-----|
| 7.1.1 | `PgVectorDocumentStore.upsert_policy_registry` | SQL INSERT … ON CONFLICT; no chunk delete |
| 7.1.2 | `PgVectorDocumentStore.get_policy_by_ref` | SELECT → `PolicyRegistryRecord` |
| 7.1.3 | `save_document` tail | After successful index: `UPDATE index_status='indexed'` |
| 7.1.4 | `save_document` skip path | If hash unchanged: still set `index_status='indexed'` if was pending |
| 7.1.5 | `InMemoryDocumentStore` | `_registry: dict[(tenant, ref), PolicyRegistryRecord]` mirror |

**Acceptance:** pgvector + memory parity in tests.

---

### Sprint 7.2 — MCP tools (~1.5h)

Add to `document_server/main.py`:

```python
POST /tools/register_policy          → register_policy(request)
POST /tools/get_policy_by_ref        → get_policy_by_ref OR 404
POST /tools/list_policy_registry     → list rows (tenant, kind?, status?)
POST /tools/sync_policy_from_catalog → catalog_sync.sync(request)
```

**`list_policy_registry` response:**

```json
{
  "tenant_id": "acme",
  "policies": [
    {
      "document_id": "...",
      "policy_ref": "drive:abc",
      "title": "...",
      "index_status": "indexed",
      "source": "google_drive"
    }
  ]
}
```

Keep **`list_policies`** unchanged (IDs only) — no breaking change.

**Health enhancement:**

```json
{ "status": "ok", "store_backend": "pgvector", "db": "ok" }
```

---

### Sprint 7.3 — Catalog sync service (~1.5h)

**File:** `document_core/services/catalog_sync.py`

```python
async def sync_policy_from_catalog(
    tenant_id: str,
    policy_ref: str,
    *,
    catalog_url: str,
    force_reindex: bool = False,
) -> IngestResult:
```

**Algorithm (minimal):**

```text
1. row = get_policy_by_ref(tenant, ref)
2. if row and row.index_status == indexed and not force_reindex:
     return early (or check hash after fetch)
3. doc = GET catalog /tenants/{t}/policies/{ref}
4. if not doc: mark failed; raise 404
5. register_policy(title, metadata from doc)  # pending if new
6. index_policy(text, document_id=stable_id, metadata.policy_ref=ref)
7. return IngestResult
```

**Config** (document-mcp only):

```env
POLICY_CATALOG_URL=http://java-backend:9000/api/v1
POLICY_SYNC_ENABLED=true
```

Add to `document_core/config.py`:

```python
policy_catalog_url: str | None = None
policy_sync_enabled: bool = True
```

**No per-source env vars.**

---

### Sprint 7.4 — Clients + review hook (~1h)

| ID | Task |
|----|------|
| 7.4.1 | `DocumentMCPClient.register_policy` |
| 7.4.2 | `DocumentMCPClient.get_policy_by_ref` |
| 7.4.3 | `DocumentMCPClient.sync_policy_from_catalog` |
| 7.4.4 | Duplicate in platform `document_client.py` |
| 7.4.5 | `policy_retrieval.py`: before `catalog.fetch_policy`, call `client.get_policy_by_ref`; if `indexed`, use `document_id` for search scope |

**Change in `resolve_policy_hits` only** (~15 lines) — not gap_retrieval unless needed.

---

### Sprint 7.5 — Docker prod defaults (~30m)

**File:** `Legal ai/docker-compose.yml` — `document-mcp`:

```yaml
environment:
  LOG_LEVEL: INFO
  DOCUMENT_STORE_BACKEND: pgvector
  DATABASE_URL: postgresql://legalai:legalai@postgres:5432/legalai
  SEARCH_BACKEND: hybrid
  POLICY_CATALOG_URL: ${POLICY_CATALOG_URL:-}
networks:
  - legalai-internal
depends_on:
  postgres:
    condition: service_healthy
```

**Do not** change `document_core` code default (`memory`) — compose overrides for prod.

Update `review_agent/.env.production.example` with cross-refs only.

---

### Sprint 7.6 — Tests (~2h)

| ID | Test file | Case |
|----|-----------|------|
| T7.1 | `document_core/tests/test_registry.py` | register pending → get_by_ref |
| T7.2 | same | index → status indexed |
| T7.3 | same | hash skip does not duplicate chunks |
| T7.4 | `document_core/tests/test_catalog_sync.py` | mock httpx catalog → sync → search hits |
| T7.5 | `review_agent/tests/test_policy_retrieval.py` | get_by_ref before catalog fetch |
| T7.6 | existing suite | 52+ tests still pass with memory backend |

---

## 10. Java team parallel track (not Python code)

Implement in Spring Boot while Python does Phase 7:

| # | Java deliverable | Blocks |
|---|------------------|--------|
| J1 | `document_registry` table | Catalog API |
| J2 | `integrations` table (source, oauth, folder config) | Sync |
| J3 | Drive sync job (service account or OAuth) | Real policies |
| J4 | Confluence sync job | Same catalog shape |
| J5 | `GET /api/v1/tenants/{t}/policies/{ref}` | Pattern B pull |
| J6 | Post-sync: HTTP call to `register_policy` + `index_policy` | Pattern A push |
| J7 | `POST /reviews` passes `contract_document_id` to Python | ID-based review |

**Java catalog row example:**

```json
{
  "policy_ref": "confluence:12345",
  "document_id": "550e8400-e29b-41d4-a716-446655440000",
  "tenant_id": "acme",
  "title": "Data Protection Policy",
  "source": "confluence",
  "index_status": "SYNCED",
  "content_hash": "abc...",
  "blob_uri": "s3://..."
}
```

Java `index_status` (SYNCED) maps to Python `pending` until `index_policy` succeeds.

---

## 11. Metadata in chunks (for report citations)

On `index_policy`, ensure metadata includes (from Java or catalog):

```json
{
  "policy_ref": "drive:abc",
  "policy_title": "Vendor Policy",
  "source": "google_drive",
  "source_location": "Legal/Policies/Vendor Policy v7",
  "version": "7",
  "department": "Legal"
}
```

Phase 6B already surfaces `policy_title` in findings — **no new report code** if metadata passed at index time.

---

## 12. Implementation order (optimized)

```text
Day 1 AM:  7.0 migration + registry service + schemas
Day 1 PM:  7.1 store methods (pgvector + memory)
Day 2 AM:  7.2 MCP tools + 7.3 catalog_sync
Day 2 PM:  7.4 clients + retrieval hook + 7.5 docker
Day 3:     7.6 tests + manual e2e with StubPolicyCatalogClient
```

```text
7.0 → 7.1 → 7.2 → 7.3 → 7.4 → 7.5 → 7.6
         └────────────────┘
              parallel: Java J1–J4
```

---

## 13. Task checklist (copy to PR)

```
[ ] 002_policy_registry_status.sql
[ ] document_core/schemas/registry.py
[ ] document_core/services/registry.py (register, get, stable_id)
[ ] document_core/services/catalog_sync.py
[ ] pgvector_store: upsert_policy_registry, get_policy_by_ref, index_status
[ ] memory_store: registry dict
[ ] main.py: register_policy, get_policy_by_ref, list_policy_registry, sync_policy_from_catalog
[ ] main.py: /health store_backend
[ ] document_core/config.py: policy_catalog_url
[ ] document_client ×2: 3 new methods
[ ] policy_catalog.py: re-export stable_id from document_core
[ ] policy_retrieval.py: registry lookup before catalog
[ ] docker-compose.yml: document-mcp pgvector
[ ] tests: test_registry.py, test_catalog_sync.py
[ ] all existing tests pass
```

---

## 14. Acceptance criteria (Phase 7 done)

- [ ] Java (or stub) can register metadata without text → `index_status=pending`
- [ ] `get_policy_by_ref` returns stable `document_id` for any `policy_ref` prefix
- [ ] `sync_policy_from_catalog` indexes text from Java catalog API
- [ ] Java push path works: direct `register_policy` + `index_policy` calls
- [ ] `docker compose up document-mcp` uses pgvector; data survives restart
- [ ] Contract-only review (`tenant_auto`) finds Java-synced policies
- [ ] No Drive/Confluence env vars in Python
- [ ] Legacy `policies[]`, `policy_refs[]` paths unchanged
- [ ] 52+ pytest tests pass

---

## 15. Out of scope (explicit)

| Item | Owner |
|------|-------|
| Google Drive OAuth + sync | Java |
| Confluence / SharePoint connectors | Java |
| Celery sync worker | Java or Phase 8 |
| PDF/DOCX parser in index path | Phase 8 (Java sends text v1) |
| `delete_policy` tombstone | Phase 8 |
| Review graph new nodes | No |
| deep_research changes | No |

---

## 16. Risk table

| Risk | Mitigation |
|------|------------|
| Breaking `list_policies` | New `list_policy_registry` tool |
| Circular import stable_id | Move to document_core; thin re-export |
| Catalog down during sync | `index_status=failed`; review uses already-indexed corpus |
| memory/pgvector drift | Same `registry.py` service for both stores |
| Java not ready | `StubPolicyCatalogClient` + `sync_policy_from_catalog` for dev |

---

*Version 2.0 — Java multi-source catalog, source-agnostic Python index + review.*
