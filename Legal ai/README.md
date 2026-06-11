# Retrieval MCP Server

Production-grade legal research MCP server for an Indian Legal AI platform. Exposes search, fetch, semantic search, and citation graph tools over HTTP with full structured logging.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Copy and configure environment
cp .env.example .env

# Start the server (local dev)
uvicorn mcp.retrieval_server.main:app --port 8001

# Optional: Streamlit test UI (in a second terminal)
# Tabs: Research Agent (platform :8080) + individual /tools/* endpoints
streamlit run streamlit_app.py
```

## Docker (with self-hosted open-webSearch)

The `open-websearch` service uses the public image from GitHub Container Registry — no local build required:

```bash
docker compose up -d open-websearch retrieval-mcp
```

If you prefer to build from source instead:

```bash
git clone https://github.com/Aas-ee/open-webSearch.git
docker build -t open-websearch:latest ./open-webSearch
# then change docker-compose.yml image back to open-websearch:latest
```

## Web Search Architecture

```
Agent / Orchestrator
        │  (MCP)
        ▼
Retrieval MCP  ── /tools/search (search_type=web) ──►  WebSearchClient
        │                                                     │ (plain HTTP)
        │                                                     ▼
        │                                          open-webSearch daemon
        │                                          (self-hosted, no API keys)
        ▼
(internal tenant docs — in-house)
```

**Phase 2:** Set `WEBSEARCH_BACKEND=legal-index` to query the Postgres FTS index built by the `crawler/` worker. No changes to `/tools/search` or `search_service`.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| POST | `/tools/search` | Unified search (web, internal, all) |
| POST | `/tools/fetch_and_extract` | Fetch full document + extract sections |
| POST | `/tools/semantic_search` | Semantic vector search (pgvector) |
| POST | `/tools/citation_graph` | Citation graph traversal |
| POST | `/tools/ingest_internal` | Ingest tenant internal document |

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `WEBSEARCH_BACKEND` | `duckduckgo` | `duckduckgo`, `open-websearch`, or `legal-index` |
| `WEBSEARCH_BASE_URL` | `http://open-websearch:3000` | open-webSearch URL (when backend=open-websearch) |
| `LOG_LEVEL` | `INFO` | Logging verbosity |
| `EXTERNAL_TIMEOUT_SECONDS` | `30` | HTTP timeout for all backends |
| `DATABASE_URL` | `postgresql://legalai:legalai@postgres:5432/legalai` | Postgres + pgvector |
| `EMBEDDING_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Local embedding model (384-dim) |
| `SEMANTIC_HYBRID_ALPHA` | `0.5` | FTS vs vector blend for hybrid search |
| `PAGE_FETCH_USER_AGENT` | LegalAI-Fetcher/1.0 | User-Agent for page fetching |

No API keys required for web search.

## Logging

All logs include a `request_id` correlation ID. JSON output goes to stdout; human-readable lines go to `logs/retrieval_mcp.log` (10 MB rotating, 5 backups).

### Log Levels

| Level | What you see |
|-------|-------------|
| **DEBUG** | Individual result titles/scores, filter dicts, section extraction details |
| **INFO** | Request lifecycle, per-source counts, `calling internal search backend`, startup config |
| **WARNING** | Degraded paths: backend timeout/skipped, missing tenant_id |
| **ERROR** | Unhandled failures, all sources failed |

### Grep by request_id

```bash
grep "request_id=8f3a" logs/retrieval_mcp.log
```

## Phase 2 — Database setup

Uses Postgres with the `pgvector` extension (included in `docker-compose.yml`):

```bash
# Apply schema (first deploy)
psql $DATABASE_URL -f db/migrations/001_init.sql

# Or via Docker
docker compose exec postgres psql -U legalai -d legalai -f /path/to/001_init.sql
```

Activate crawler seeds when ready:

```sql
UPDATE seed_sources SET active = true WHERE domain = 'livelaw.in';
```

## Crawler (Phase 2)

Separate worker package at `crawler/` — not in the FastAPI request path. Crawls legal domains, stores clean text + embeddings in `web_documents`.

```bash
pip install -r crawler/requirements.txt
scrapy crawl legal -a seed_id=1
celery -A crawler.tasks worker --loglevel=info
celery -A crawler.tasks beat --loglevel=info
```

## Example: Ingest internal document

```bash
curl -X POST http://localhost:8001/tools/ingest_internal \
  -H "Content-Type: application/json" \
  -d '{"tenant_id": "acme-corp", "title": "NDA Policy", "text": "Confidentiality applies for 2 years..."}'
```

Then search it:

```bash
curl -X POST http://localhost:8001/tools/search \
  -H "Content-Type: application/json" \
  -d '{"query": "confidentiality period", "search_type": "internal", "tenant_id": "acme-corp"}'
```

## Tests

```bash
pytest mcp/retrieval_server/tests/ crawler/tests/ -v
```

All tests mock HTTP backends — no real network calls.

## Example Search Request

```bash
curl -X POST http://localhost:8001/tools/search \
  -H "Content-Type: application/json" \
  -d '{"query": "non-compete enforceable", "search_type": "web", "max_results": 10}'
```
