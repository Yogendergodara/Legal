# MCP Retrieval Server — Flow Diagram

```mermaid
flowchart TD
    UQ([User Query]) --> ROUTER{MCP Endpoint Router}

    %% ─────────── TOOL 1: /tools/search ───────────
    ROUTER -->|POST /tools/search| SEARCH[SearchService]
    SEARCH --> ST{search_type?}

    ST -->|"all"| FANOUT[Fan-out to all sources\nasyncio.gather]
    ST -->|"web"| SINGLE_WEB[Single source:\nWeb]
    ST -->|"internal"| SINGLE_INT[Single source:\nInternal Docs\nrequires tenant_id]

    %% Fan-out paths
    FANOUT --> FW[WebSearchClient]
    FANOUT -->|only if tenant_id| FI[InternalDocsClient]

    %% Single-source paths
    SINGLE_WEB --> FW
    SINGLE_INT --> FI

    %% Web search backends
    FW --> WB{websearch_backend?}
    WB -->|open-websearch| OWS[open-webSearch daemon\nself-hosted HTTP]
    WB -->|legal-index| LI[Postgres FTS\ncrawler.fts.search_documents]

    %% Web search response
    OWS --> WEBMAP[Map raw items\nto SearchResult\nsource_type=web]
    LI --> WEBMAP

    %% Internal docs response
    FI --> INTRES[SearchResult list]

    %% Merge and rank
    WEBMAP --> MERGE[Merge all results]
    INTRES --> MERGE

    MERGE --> DEDUP[Deduplicate by source_id\nkeep highest relevance_score]
    DEDUP --> RANK[Sort by relevance_score DESC\ntruncate to max_results]
    RANK --> SR([SearchResponse\nresults + degraded flag\n+ search_time_ms])

    %% ─────────── TOOL 2: /tools/fetch_and_extract ───────────
    ROUTER -->|POST /tools/fetch_and_extract| FETCH[FetchService]
    FETCH --> FST{source_type?}

    FST -->|web| FETCHWEB[fetch_clean_text\nvia trafilatura\nHTTP page scrape]
    FST -->|internal| FETCHINT[Tenant document lookup]

    FETCHWEB --> SECT[Extract sections\nfull_text / custom]
    FETCHINT --> SECT

    SECT --> FR([FetchResponse\ntitle + full_text\n+ extracted sections\n+ fetch_time_ms])

    %% ─────────── TOOL 3: /tools/semantic_search ───────────
    ROUTER -->|POST /tools/semantic_search| SEMSVC[SemanticSearchService]
    SEMSVC --> SEMRES([SemanticSearchResponse])

    %% ─────────── TOOL 4: /tools/citation_graph ───────────
    ROUTER -->|POST /tools/citation_graph| CITSVC[CitationService]
    CITSVC --> CITRES([CitationGraphResponse\nstored citation edges])

    %% ─────────── HEALTH ───────────
    ROUTER -->|GET /health| HEALTH([HealthResponse\nstatus ok])

    %% ─────────── Error handling ───────────
    RANK -->|all sources failed| ERR502([HTTP 502\nAll search sources failed])
    RANK -->|unexpected error| ERR500([HTTP 500\nInternal server error])

    %% Styling
    classDef endpoint fill:#4A90D9,color:#fff,stroke:#2c6fad
    classDef service fill:#6B4FA8,color:#fff,stroke:#4a3278
    classDef external fill:#E67E22,color:#fff,stroke:#b35a0f
    classDef stub fill:#888,color:#fff,stroke:#555,stroke-dasharray:5 5
    classDef response fill:#27AE60,color:#fff,stroke:#1a7a42
    classDef error fill:#E74C3C,color:#fff,stroke:#b03a2e
    classDef decision fill:#F39C12,color:#fff,stroke:#b5770d

    class SEARCH,FETCH service
    class OWS,LI external
    class SR,FR,SEMRES,CITRES,HEALTH response
    class ERR502,ERR500 error
    class ROUTER,ST,FST,WB decision
```

## Summary of All Retrieval Types

| Tool | Endpoint | Source | Phase |
|------|----------|--------|-------|
| Unified Search | `/tools/search` | **Web (open-webSearch)** — self-hosted HTTP daemon | Live |
| Unified Search | `/tools/search` | **Web (legal-index)** — Postgres FTS over crawled docs | Live |
| Unified Search | `/tools/search` | **Internal Docs** — tenant-scoped private documents | Live |
| Fetch & Extract | `/tools/fetch_and_extract` | **Web pages** — trafilatura HTTP scrape | Live |
| Fetch & Extract | `/tools/fetch_and_extract` | **Internal Docs** — tenant document store | Live |
| Semantic Search | `/tools/semantic_search` | **Vector store** — embedding similarity | Live |
| Citation Graph | `/tools/citation_graph` | **Graph traversal** — stored citation edges | Live |
