# MCP global concurrency limiter

**Status:** IMPLEMENTED  
**Goal:** Stop `circuit_breaker:mcp → OPEN` under `parallel_hybrid` without switching to `serial`, preserving accuracy (no skipped searches).

## Problem

- `DocumentMCPClient` has no global in-flight cap; stage concurrency (8+8) multiplies with parallel queries and hybrid retrieval.
- After 5 MCP failures, breaker opens → searches skipped → false IPC / missed NC.
- LLM already has `LLM_GLOBAL_CONCURRENCY` in `llm_gateway.py`; MCP did not.

## Design

| Decision | Choice |
|----------|--------|
| Scope | **Process-global** module singleton (`mcp_limiter.py`), not per `DocumentMCPClient` instance |
| Queueing | **Block-and-wait** on `asyncio.Semaphore` (v1, mirrors LLM) |
| Acquire timeout | `MCP_SEMAPHORE_ACQUIRE_TIMEOUT_SECONDS` (default 60); fail with `RecoverableError` + metric |
| Contention signal | Warn when wait ≥ `MCP_SEMAPHORE_ACQUIRE_WARN_SECONDS` (default 30) |
| Slot lifetime | Held for full `_request` retry loop (one logical MCP call) |
| Health probe | `_wait_healthy` uses raw `httpx` — does not consume semaphore |

## Config

```env
MCP_GLOBAL_CONCURRENCY=6
MCP_SEMAPHORE_ACQUIRE_TIMEOUT_SECONDS=60
MCP_SEMAPHORE_ACQUIRE_WARN_SECONDS=30

# Stage caps (under global ceiling)
OBLIGATION_RETRIEVAL_CONCURRENCY=4
SECTION_RETRIEVAL_CONCURRENCY=4
EVIDENCE_EXPAND_CONCURRENCY=2
```

## Files

- `review_agent/resilience/mcp_limiter.py` — singleton limiter + `mcp_concurrency_slot` context manager
- `review_agent/clients/document_client.py` — wrap `_request` body
- `review_agent/config.py` — settings + runtime snapshot
- `review_agent/graph/nodes.py` — `mcp_semaphore_*` stats on artifact
- `tests/test_mcp_limiter.py` — concurrency + timeout + warn

## Validation

1. `pytest tests/test_mcp_limiter.py tests/test_circuit_breaker.py -q`
2. Atlassian smoke at `MCP_GLOBAL_CONCURRENCY` = 4, 6, 8 — pick highest with `breaker_open_events_mcp=0`
3. Compare: IPC, wall time, `mcp_semaphore_contention_events`, `mcp_semaphore_acquire_timeouts`

## Rollback

`MCP_GLOBAL_CONCURRENCY=100` (effectively off) or revert commit.
