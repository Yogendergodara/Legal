# Legal AI Platform

Orchestration layer for the Legal AI system.

## Architecture

```
Client
  ↓
API Gateway (FastAPI)
  ↓
Query Orchestrator
  ↓
Agent Registry → Research Agent (and future agents)
  ↓
RetrievalMCPClient (HTTP)
  ↓
Legal ai Retrieval Server (/tools/*)
  ↓
External Sources
```

## Quick Start

1. Start the retrieval server (from `Legal ai/`):

   ```bash
   uvicorn mcp.retrieval_server.main:app --port 8001
   ```

2. Install and run the platform:

   ```bash
   cd legal_ai_platform
   pip install -e ".[dev]"
   cp .env.example .env
   uvicorn legal_ai_platform.gateway.app:app --host 0.0.0.0 --port 8080
   ```

3. Submit a query:

   ```bash
   curl -X POST http://localhost:8080/query \
     -H "Content-Type: application/json" \
     -d '{"query": "What is the limitation period for breach of contract in India?"}'
   ```

## Multi-turn (clarification) sessions

The Research Agent may ask a clarifying question before researching. When the
response has `"awaiting_input": true`, reply by sending the **same** `thread_id`
returned in that response:

```bash
# First call — note the thread_id and awaiting_input in the response
curl -X POST http://localhost:8080/query \
  -H "Content-Type: application/json" \
  -d '{"query": "I need help with a contract dispute"}'

# Follow-up — reuse the returned thread_id to continue the conversation
curl -X POST http://localhost:8080/query \
  -H "Content-Type: application/json" \
  -d '{"query": "It is a SaaS vendor agreement governed by Indian law", "thread_id": "<thread_id-from-previous-response>"}'
```

Sessions are held in an in-memory checkpointer, so they reset on restart. Swap
`MemorySaver` for a persistent checkpointer (e.g. Postgres) for durability.

> `AGENT_TIMEOUT_SECONDS` (default 300) bounds a single run; set `0` to disable.

## Adding a New Agent

1. Create `agents/<name>/<name>_agent.py` inheriting from `BaseAgent`.
2. Register it in `container.py`:

   ```python
   registry.register("contract", ContractAgent(...))
   ```

3. Add classification rules in `orchestration/classifier.py`.

No orchestrator code changes required.
