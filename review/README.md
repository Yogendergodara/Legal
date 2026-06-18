# Contract Review (LangGraph library)

Review **logic** lives here. The **public API** is only:

```text
legal_ai_platform  →  POST /query  (task_type: review)
```

## Layout

```text
review/review_agent/
├── graph/           LangGraph pipeline
├── clients/         DocumentMCPClient (used in tests)
├── dimensions/      review_dimensions.yaml (static mode only)
├── state/
├── services/
└── reports/
```

Supporting packages (outside `review/`):

- `document_core/` — ingest, search, grounding library
- `Legal ai/mcp/document_server/` — MCP tools HTTP server

## Run via unified gateway

```bash
# Start document-mcp + platform (see legal_ai_platform/README.md)
curl -X POST http://localhost:8080/query \
  -H "Content-Type: application/json" \
  -d '{
    "task_type": "review",
    "contract_text": "...",
    "policies": [{"title": "Policy", "text": "..."}]
  }'
```

## Memory (shared with research agent)

Review uses **retrieval-mcp** memory tools (same `MEMORY.md` store as research):

```text
load_memory   →  POST /tools/memory/search  (before review)
save_memory   →  POST /tools/memory/save    (after report)
```

Pass `thread_id` on `POST /query` to resume the LangGraph session checkpoint.

## LangGraph flow

```text
load_memory → index_policies → contract_parser → clause_detection
  → policy_plan → policy_retrieval → compliance_review → grounding → report → save_memory
```

## Compliance review modes

Compliance compares **retrieved policy parent section** vs **contract parent section** per category.

| Mode | Env | Use |
|------|-----|-----|
| `llm` (default) | `COMPLIANCE_MODE=llm` | Production — LLM judges only against supplied policy text |
| `lexical` | `COMPLIANCE_MODE=lexical` | Legacy word-overlap; CI / no API key |

### Review plan (Phase 1 — dynamic default)

| Mode | Env | Use |
|------|-----|-----|
| `dynamic` (default) | `REVIEW_PLAN_MODE=dynamic` | Categories from indexed **policy sections** (`list_policies` + `list_sections`) |
| `static` | `REVIEW_PLAN_MODE=static` | Legacy `review_dimensions.yaml` checklist (dev/CI only) |

`REVIEW_MAX_CATEGORIES` (default 30) caps cost on large playbooks.

### Policy catalog fetch (Phase 2)

| Setting | Purpose |
|---------|---------|
| `POLICY_CATALOG_URL` | External catalog base URL (Java/Drive later) |
| `POLICY_FETCH_ENABLED` | Enable catalog fetch (default `true`) |
| `POLICY_SEARCH_TOP_K` | Search breadth on retrieval retry |

Request `policy_refs: ["opaque-id"]` on `POST /query` to fetch policies not inlined in `policies[]`. Retrieval ladder: `get_section` → search → catalog fetch → retry.

### Optional LLM category filter (Phase 3)

| Setting | Default | Purpose |
|---------|---------|---------|
| `REVIEW_PLAN_LLM_FILTER` | `false` | LLM filters pre-built categories for large playbooks |
| `REVIEW_PLAN_LLM_FILTER_MIN_CATEGORIES` | `15` | Skip filter when category count at or below this |

Prompt: `prompts/policy_plan.md` — subset of category IDs only; fail-open on error. Compare prompt stays in `compliance_review.md`.

**LLM mode** (`review_agent/services/compliance_llm.py`):

- Skips LLM when no policy hits → `INSUFFICIENT_POLICY_CONTEXT`
- Skips LLM when no contract hits → `INCONCLUSIVE`
- Prompt: `review_agent/prompts/compliance_review.md` (policy-only judgment, verbatim quotes)
- Validates `contract_quote` / `policy_quote` are exact substrings; invalid → `INCONCLUSIVE`
- Retries on parse failure (`COMPLIANCE_LLM_MAX_RETRIES`)
- `review_dimensions.yaml` → static mode only (`REVIEW_PLAN_MODE=static`)

Copy `review_agent/.env.example` → `.env` and set `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` (same pattern as research agent).

### Production configuration (Phase 6B)

| Setting | Dev / CI (default) | Production (`.env.production.example`) |
|---------|-------------------|----------------------------------------|
| `REVIEW_POLICY_SOURCE` | `request` | `tenant_auto` (contract only) |
| `COMPLIANCE_MODE` | `llm` (CI: `lexical`) | `hybrid` |
| `REVIEW_POLICY_SCOPE` | `request` | `discovered` |
| `DOCUMENT_STORE_BACKEND` | `memory` | `pgvector` |

**QA before prod flip:** policies indexed for tenant; contract-only `/query` discovers playbooks; report shows playbook title on violations.

## Development

**Implementation plans (dynamic review):** see [`plans/`](./plans/) — Phase 1 (dynamic plan), Phase 2 (fetch/retry), Phase 3 (LLM filter).

Run graph tests without the platform:

```bash
cd review/review_agent
pip install -e ".[dev]" -e ../../document_core
pytest tests/
```

Do **not** run `review_agent.api.app` — that entry was removed in favour of one gateway.
