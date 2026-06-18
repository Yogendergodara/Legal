# Phase 9 — Postgres Session & Long-Term Memory

**Plan ID:** `DR-PHASE-9`  
**Status:** Implemented (9A session + 9B memory)  
**Principle:** **Minimal diff** — swap storage backend only; orchestrator flow unchanged  
**Depends on:** Existing `SessionService` + `QueryOrchestrator` (load/save per `/query` already works)

---

## 1. Decision: vector vs normal Postgres

| Data | Store | Why |
|------|-------|-----|
| **Session header** (`thread_id`, `summary`, `updated_at`) | **Normal Postgres** | Load by key — no semantic search |
| **Chat turns** (`user` / `assistant` rows) | **Normal Postgres** | Append-only by `thread_id`, ordered by time |
| **Matter snapshot** (contract text/IDs, policies, last report) | **Postgres JSONB** | Structured blob, load with session |
| **Long-term memory** (durable facts, not full chat) | **Postgres text + optional `vector(768)`** | v1: **keyword/tenant filter**; v1.1: hybrid search like policies |

**Do NOT use pgvector for chat turns.**  
**Do NOT mix session tables with `document_chunks` (policy RAG).** Same Postgres **instance**, separate **tables**.

**Skip Qdrant** — reuse existing pgvector extension + ModernBERT if we add semantic memory search later.

---

## 2. What already works (keep as-is)

```text
POST /query
  → session_svc.load_or_create(thread_id)      # today: JSON file
  → append_user_turn → agent.execute()
  → append_assistant_turn → update_summary
  → session_svc.persist(session)                 # today: JSON file
```

**Only change:** `load` / `persist` target **Postgres** instead of `SessionFileStore`.

**In RAM during one request:** `SessionState` object — **keep** (normal, optimal).

**Do NOT change:** `orchestrator.py` control flow, agent APIs, review graph, document-mcp RAG.

---

## 3. Target architecture

```text
                    POST /query
                         │
                         ▼
              QueryOrchestrator (unchanged flow)
                         │
                         ▼
              SessionService (unchanged API)
                         │
            ┌────────────┴────────────┐
            ▼                         ▼
   SESSION_STORE_BACKEND      SESSION_STORE_BACKEND
        =postgres                   =file (dev fallback)
            │                         │
            ▼                         ▼
   SessionPostgresStore         SessionFileStore (existing)

Long-term memory (platform MemoryBridge):
            │
   MEMORY_STORE_BACKEND=postgres (new, optional)
            ▼
   platform_memory table (+ optional embedding)
   OR keep retrieval-mcp file until 9B
```

**Same Postgres as compose `legalai-postgres`** — new schema `platform` or table prefix `platform_*`.

---

## 4. Database schema (migration `003_platform_session.sql`)

**File:** `Legal ai/db/migrations/003_platform_session.sql`

```sql
-- Session chat (normal relational — NOT pgvector)

CREATE TABLE IF NOT EXISTS platform_sessions (
    tenant_id     TEXT NOT NULL,
    thread_id     TEXT NOT NULL,
    summary       TEXT NOT NULL DEFAULT '',
    matter        JSONB NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, thread_id)
);

CREATE INDEX IF NOT EXISTS ix_platform_sessions_tenant_updated
    ON platform_sessions (tenant_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS platform_session_turns (
    id            BIGSERIAL PRIMARY KEY,
    tenant_id     TEXT NOT NULL,
    thread_id     TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content       TEXT NOT NULL,
    agent         TEXT,
    task_type     TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_platform_session_turns_thread
    ON platform_session_turns (tenant_id, thread_id, created_at);

-- Long-term memory (facts only — optional vector in 9B)
CREATE TABLE IF NOT EXISTS platform_memory (
    id            BIGSERIAL PRIMARY KEY,
    tenant_id     TEXT NOT NULL,
    thread_id     TEXT,
    agent         TEXT NOT NULL,
    title         TEXT NOT NULL,
    content       TEXT NOT NULL,
    hook          TEXT NOT NULL DEFAULT '',
    embedding     vector(768),          -- NULL ok in v1
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_platform_memory_tenant
    ON platform_memory (tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_platform_memory_tsv
    ON platform_memory USING gin (to_tsvector('english', title || ' ' || content));
```

**Matter JSONB** stores existing `MatterSnapshot` fields (`contract_text`, `policies`, `last_review_report`, …).

**Turns:** store full transcript in `platform_session_turns`; session row holds summary + matter only.

---

## 5. Storage protocol (minimal abstraction)

**New file:** `legal_ai_platform/session/store.py` (~40 lines)

```python
class SessionStore(Protocol):
    def load(self, tenant_id: str, thread_id: str) -> SessionState | None: ...
    def save(self, state: SessionState) -> None: ...
    def exists(self, tenant_id: str, thread_id: str) -> bool: ...
    def delete(self, tenant_id: str, thread_id: str) -> bool: ...
```

- `SessionFileStore` — **rename/move** from `file_store.py`, implement protocol (no logic change)
- `SessionPostgresStore` — **new** (~120 lines)

**Change `SessionService`:** constructor takes `SessionStore` instead of concrete `SessionFileStore` (~5 lines).

**Change `container.py`:** factory by env (~15 lines).

---

## 6. Config (minimal)

**File:** `legal_ai_platform/config.py`

```python
session_store_backend: Literal["file", "postgres"] = "file"
database_url: str | None = None
memory_store_backend: Literal["mcp", "postgres"] = "mcp"  # long-term; mcp = current file path
```

**File:** `legal_ai_platform/.env.example`

```env
SESSION_STORE_BACKEND=postgres
DATABASE_URL=postgresql://legalai:legalai@localhost:5435/legalai
MEMORY_STORE_BACKEND=mcp
```

**Docker:** platform service gets `DATABASE_URL` + `SESSION_STORE_BACKEND=postgres` when added to compose.

**Dev fallback:** `SESSION_STORE_BACKEND=file` — zero Postgres required for local quick test.

---

## 7. SessionPostgresStore behavior (precise)

### 7.1 `load(tenant_id, thread_id)`

1. `SELECT summary, matter FROM platform_sessions WHERE tenant_id=? AND thread_id=?`
2. If missing → return `None` (orchestrator creates new `SessionState`)
3. `SELECT ... FROM platform_session_turns WHERE ... ORDER BY created_at` (cap optional: last 500 turns)
4. Build `SessionState(thread_id, tenant_id, summary, turns, matter=MatterSnapshot(**matter))`

### 7.2 `save(state)`

**One transaction:**

1. `INSERT INTO platform_sessions ... ON CONFLICT DO UPDATE` (summary, matter, updated_at)
2. **Turns strategy (minimal):** append-only — compare `len(state.turns)` vs DB count; insert **only new tail turns** (avoid rewriting full transcript each save)

```python
# Pseudocode
existing_count = COUNT turns in DB
for turn in state.turns[existing_count:]:
    INSERT platform_session_turns ...
```

3. Commit

**Why append-only:** orchestrator already mutates in-memory list; we never edit old turns.

### 7.3 `delete`

`DELETE FROM platform_session_turns WHERE ...; DELETE FROM platform_sessions WHERE ...`

---

## 8. Long-term memory (Phase 9B — optional same PR or follow-up)

**Today:** `MemoryBridge` → retrieval-mcp → `MEMORY.md` files.

**Minimal postgres path:**

**New file:** `legal_ai_platform/session/memory_postgres.py` (~80 lines)

| Method | Behavior |
|--------|----------|
| `search(tenant_id, queries[])` | `SELECT ... WHERE tenant_id=? AND tsv @@ plainto_tsquery(...)` top 5 |
| `save(title, content, hook, tenant_id, thread_id, agent)` | `INSERT INTO platform_memory` |

**Wire in `MemoryBridge`:** if `MEMORY_STORE_BACKEND=postgres`, use postgres store; else keep MCP client (no break).

**v1.1 (later):** embed `content` with ModernBERT on save; search with hybrid FTS + vector (same as document-mcp).

**Do NOT remove MCP memory path** until postgres path tested — env switch only.

---

## 9. Implementation sprints

### Sprint 9A — Postgres session store (~1–2 days)

| ID | Task | File(s) | Lines | Acceptance |
|----|------|---------|------:|------------|
| 9A.1 | Migration `003_platform_session.sql` | `Legal ai/db/migrations/` | 45 | `init_db.py` applies |
| 9A.2 | `SessionStore` protocol | `session/store.py` | 40 | File + PG implement |
| 9A.3 | Refactor `SessionFileStore` to protocol | `session/file_store.py` | 10 | No behavior change |
| 9A.4 | `SessionPostgresStore` load/save/delete | `session/postgres_store.py` | 120 | Round-trip test |
| 9A.5 | `SessionService` accepts `SessionStore` | `session/service.py` | 5 | Type only |
| 9A.6 | `container.py` factory | `container.py` | 15 | env `postgres` works |
| 9A.7 | Config + `.env.example` | `config.py`, `.env.example` | 15 | Documented |
| 9A.8 | Tests with postgres (skip if no DB) | `tests/test_session_postgres.py` | 90 | load/save/delete |
| 9A.9 | Gateway tests use postgres conftest | `tests/conftest.py` | 20 | Existing tests pass |

**Total 9A:** ~360 lines

### Sprint 9B — Postgres long-term memory (~1 day, optional)

| ID | Task | File(s) | Lines | Acceptance |
|----|------|---------|------:|------------|
| 9B.1 | `PostgresMemoryStore` search/save | `session/memory_postgres.py` | 80 | FTS finds saved review |
| 9B.2 | `MemoryBridge` backend switch | `memory_bridge.py` | 25 | `MEMORY_STORE_BACKEND=postgres` |
| 9B.3 | Tests | `tests/test_memory_postgres.py` | 60 | save + search |

**Total 9B:** ~165 lines

### Sprint 9C — Ops (~0.5 day)

| ID | Task | Acceptance |
|----|------|------------|
| 9C.1 | Add platform to `docker-compose` with `DATABASE_URL` | Session survives container restart |
| 9C.2 | Document Java handoff: Java can own same tables later OR call Python gateway | API contract note |

---

## 10. What we explicitly do NOT change (minimal scope)

| Item | Reason |
|------|--------|
| `orchestrator.py` flow | Already load/save per turn |
| LangGraph `MemorySaver` | Separate concern; Phase 10 |
| `document-mcp` / policy RAG tables | Already pgvector |
| Review / research graph nodes | No agent changes |
| Redis cache | Defer until multi-region scale |
| Migrate old JSON sessions automatically | Optional one-off script; not required for v1 |

---

## 11. Handling edge cases

| Case | Handling |
|------|----------|
| Postgres down | Log error; fail `/query` with 503 OR fallback to file if `SESSION_STORE_FALLBACK=file` (optional env) |
| Huge transcript | Load last N turns (config `session_transcript_load_limit=500`); summary still on session row |
| Large `contract_text` in matter | Keep in JSONB; consider storing `contract_document_id` only in Phase 8 |
| Concurrent writes same thread | Last write wins on session row; turns append-only reduces conflict |
| Delete session | Existing `DELETE /sessions/{id}` — delete postgres rows |

---

## 12. Java alignment (parallel, no Python block)

Java may later own `platform_sessions` tables via same schema. Until then Python platform writes them.

```text
Java UI → POST /query (thread_id) → Python orchestrator → Postgres session
Java admin → GET /sessions/{thread_id}  (future: read same tables)
```

---

## 13. Acceptance criteria (Phase 9 done)

- [x] `SESSION_STORE_BACKEND=postgres` — session survives platform restart  
- [x] Two `/query` turns same `thread_id` — second turn sees first transcript  
- [x] `GET/DELETE /sessions/{thread_id}` works with postgres store  
- [x] `SESSION_STORE_BACKEND=file` still works (dev fallback)  
- [x] Orchestrator code path unchanged (grep: no new calls in orchestrator)  
- [x] Review + research agents unchanged  
- [x] Optional 9B: long-term memory in `platform_memory` with FTS search  

---

## 14. Implementation order

```text
9A.1 migration
9A.2–9A.4 SessionPostgresStore
9A.5–9A.7 wire config + container
9A.8–9A.9 tests
9B (optional) long-term memory postgres
9C docker
```

---

## 15. Task checklist (copy to PR)

```
[ ] 003_platform_session.sql
[ ] session/store.py (Protocol)
[ ] session/postgres_store.py
[ ] session/file_store.py implements Protocol
[ ] session/service.py → SessionStore
[ ] container.py factory
[ ] config.py SESSION_STORE_BACKEND + DATABASE_URL
[ ] tests/test_session_postgres.py
[ ] .env.example updated
[ ] (optional) memory_postgres.py + MemoryBridge switch
[ ] docker-compose platform service env
```

**Estimated total:** ~360 lines (9A) + ~165 lines (9B optional)

---

*Vector for policies (document-mcp) stays separate. Session chat uses normal Postgres. Long-term memory: Postgres text v1, optional vector v1.1.*
