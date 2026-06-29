# Dynamic Review — Implementation Plans

Three phased plans to replace static YAML-driven review with **production-grade dynamic policy review**.

| Plan | ID | Status | Depends on |
|------|-----|--------|------------|
| **Phase 1** — Dynamic Review Plan | `DR-PHASE-1` | Implemented |
| **Phase 2** — Policy Fetch & Retry | `DR-PHASE-2` | Implemented |
| [Phase 3 — Prompt Split (LLM Filter)](./PHASE3_PROMPT_SPLIT_PLAN.md) | `DR-PHASE-3` | Implemented |
| [Phase 4 — Persistent RAG Store](./PHASE4_PERSISTENT_RAG_STORE_PLAN.md) | `DR-PHASE-4` | Partial (4A–4C core) |
| [Phase 5 — Hybrid Batch Compliance](./PHASE5_HYBRID_COMPLIANCE_PLAN.md) | `DR-PHASE-5` | Implemented (core) |
| [Phase 6 — Contract-First Discovery](./PHASE6_CONTRACT_FIRST_DISCOVERY_PLAN.md) | `DR-PHASE-6` | Implemented (core) |
| [Phase 6B — Output Polish & Prod Defaults](./PHASE6B_OUTPUT_POLISH_PLAN.md) | `DR-PHASE-6B` | Implemented |
| [Phase 7 — Java Catalog Integration](./PHASE7_JAVA_CATALOG_INTEGRATION_PLAN.md) | `DR-PHASE-7` | Implemented |
| [Java Catalog API Contract](./JAVA_CATALOG_API_CONTRACT.md) | — | Spec |
| [Phase 9 — Postgres Session & Memory](../legal_ai_platform/docs/PHASE9_POSTGRES_SESSION_MEMORY_PLAN.md) | `DR-PHASE-9` | Implemented |
| [Phase 10 — Section-First + High-Recall Retrieval](./PHASE10_SECTION_FIRST_RETRIEVAL_PLAN.md) | `DR-PHASE-10` | v1 shipped |
| [Phase 10 — Production unified (single pipeline)](./PHASE10_PRODUCTION_UNIFIED_IMPL_PLAN.md) | `DR-PHASE-10-PROD` | **Execute next** |
| [Phase R — AI-first semantic routing (obligation → catalog → evidence)](./PHASE_R_SEMANTIC_ROUTING_PLAN.md) | `DR-PHASE-R` | **R0–R9 implemented** |
| [Phase R0+R1 — Implementation detail (minimal code)](./PHASE_R0_R1_IMPLEMENTATION_PLAN.md) | `DR-PHASE-R0-R1` | **Implemented** |
| [Phase R2+R3 — Planner + catalog match (minimal code)](./PHASE_R2_R3_IMPLEMENTATION_PLAN.md) | `DR-PHASE-R2-R3` | **Implemented** |
| [Phase R4+R5 — Scoped retrieval + evidence sufficiency (minimal code)](./PHASE_R4_R5_IMPLEMENTATION_PLAN.md) | `DR-PHASE-R4-R5` | **Implemented** |
| [Phase R6+R7 — Obligation compare + audit (minimal code)](./PHASE_R6_R7_IMPLEMENTATION_PLAN.md) | `DR-PHASE-R6-R7` | **Implemented** |
| [Phase R8+R9 — Golden CI + rollout (minimal code)](./PHASE_R8_R9_IMPLEMENTATION_PLAN.md) | `DR-PHASE-R8-R9` | **Implemented** |
| [Phase P0–P5 — Engine recovery (E2E IPC flood)](./PHASE_P0_P5_ENGINE_RECOVERY_MASTER_PLAN.md) | `DR-PHASE-P0-P5` | **Planned** |
| [Phase P1 — Section retrieval (detail)](./PHASE_P1_SECTION_RETRIEVAL_IMPLEMENTATION_PLAN.md) | `DR-PHASE-P1` | Implemented |
| [Phase P2 — LLM resilience (detail)](./PHASE_P2_LLM_RESILIENCE_IMPLEMENTATION_PLAN.md) | `DR-PHASE-P2` | Implemented |
| [Phase P0 — IPC flood (detail)](./PHASE_P0_IPC_FLOOD_IMPLEMENTATION_PLAN.md) | `DR-PHASE-P0` | Planned |
| [Phase P3 — Compare validation (detail)](./PHASE_P3_COMPARE_VALIDATION_IMPLEMENTATION_PLAN.md) | `DR-PHASE-P3` | Implemented |
| [Phase P4 — Final verify / re-compare (detail)](./PHASE_P4_RECOVERY_IMPLEMENTATION_PLAN.md) | `DR-PHASE-P4` | Implemented |
| [Phase P5 — Diagnostics / E2E validation (detail)](./PHASE_P5_DIAGNOSTICS_IMPLEMENTATION_PLAN.md) | `DR-PHASE-P5` | Investigation complete |
| [**IPC Remediation — Master plan (code-verified)**](./PHASE_IPC_REMEDIATION_MASTER_PLAN.md) | `DR-PHASE-IPC` | Planned |
| [**IPC-0 — Config quick wins (implementation)**](./PHASE_IPC0_CONFIG_IMPLEMENTATION_PLAN.md) | `DR-PHASE-IPC-0` | **IMPLEMENTED** — restart Dev UI + re-sync for 0.7 |
| [**IPC-1 — Small code fixes (implementation)**](./PHASE_IPC1_CODE_FIXES_IMPLEMENTATION_PLAN.md) | `DR-PHASE-IPC-1` | **IMPLEMENTED** |
| [**IPC-2 — Sync / index quality (implementation)**](./PHASE_IPC2_SYNC_INDEX_QUALITY_IMPLEMENTATION_PLAN.md) | `DR-PHASE-IPC-2` | **Execute next** (re-sync) |
| [**IPC-3 — Discovery + retrieval tuning (implementation)**](./PHASE_IPC3_DISCOVERY_RETRIEVAL_TUNING_PLAN.md) | `DR-PHASE-IPC-3` | **IMPLEMENTED** |
| [**IPC-4 — Compare / recovery / dedupe (implementation)**](./PHASE_IPC4_COMPARE_RECOVERY_DEDUPE_PLAN.md) | `DR-PHASE-IPC-4` | **IMPLEMENTED** (4.5 deferred) |
| [**IPC-5 — Validation & observability (implementation)**](./PHASE_IPC5_VALIDATION_OBSERVABILITY_PLAN.md) | `DR-PHASE-IPC-5` | **IMPLEMENTED** |
| [**Parallel graph hardening (PF-1C2)**](./PHASE_PARALLEL_GRAPH_HARDENING_PLAN.md) | `DR-PHASE-PG` | **Implemented** — PG-1–PG-7 (retrieval chain, ipc_fallback, join gate, merge supersede, fail-open, E2E invoke, serial default) |
| [**Phase B — Dynamic retry resilience (429 + INCONCLUSIVE)**](./PHASE_B_RETRY_RESILIENCE_PLAN.md) | `DR-PHASE-B` | **Planned** — failure classifier + review posture; no global LLM off |
| [**RC-03/04 — Funnel zero & tenant isolation**](./PHASE_RC0304_FUNNEL_TENANT_FIX_PLAN.md) | `DR-PHASE-RC0304` | **IMPLEMENTED** — `compare_queued=0` + tenant isolation; IPC-2/3/5 |
| [**RC-05/06/07 — Cap, false IPC & F5 recovery**](./PHASE_RC050607_CAP_IPC_F5_RECOVERY_PLAN.md) | `DR-PHASE-RC050607` | **IMPLEMENTED** — cap 80, F5 gap promotion, NC/regression flags |
| [**RC-08/09/10 — Routing pilot, compare universe & extract structure**](./PHASE_RC080910_ROUTING_COMPARE_EXTRACT_PLAN.md) | `DR-PHASE-RC080910` | **IMPLEMENTED** — tenant allowlists, hit scope, extract HOT recovery |
| [**RC-11/12/13 — Cisco score, LLM profile & fast-wall symptom**](./PHASE_RC111213_CISCO_PROFILE_WALL_PLAN.md) | `DR-PHASE-RC111213` | **IMPLEMENTED** — legal score gate, mistral_conservative golden, wall+NC flags |
| [**RC-14/15/16 — Quote repair fail-open, IPC hierarchy & D–G clarification**](./PHASE_RC141516_QUOTE_IPC_CLARIFICATION_PLAN.md) | `DR-PHASE-RC141516` | **IMPLEMENTED** — grounding 429 fail-open, IPC 0.50–0.65 post-IPC-2 only, RCA |
| [**SR-01 — Meaning-first retrieval & precision tuning**](./PHASE_SR01_MEANING_FIRST_RETRIEVAL_PLAN.md) | `DR-PHASE-SR01` | **IMPLEMENTED** (SR1+SR3 minimal) — meaning-first query, soft precision, A/B harness |
| [**OB-01–04 — Non-429 IPC recovery (obligation funnel + validation)**](./PHASE_OB01020304_NON429_IPC_RECOVERY_PLAN.md) | `DR-PHASE-OBIPC` | **IMPLEMENTED** — parallel skip guard, validation fence, evidence tune, IPC report |
| [**PR-01 — Precision funnel recovery (post-OB, excl. 429)**](./PHASE_PR01_PRECISION_FUNNEL_RECOVERY_PLAN.md) | `DR-PHASE-PR01` | **IMPLEMENTED** — rerank bypass, catalog marginal compare, boilerplate precision, expand defaults |

## Phase 10 (accuracy — production cutover)

Section-first LLM review + multi-path retrieval. v1 dual-mode shipped; **next:** [Production unified plan](./PHASE10_PRODUCTION_UNIFIED_IMPL_PLAN.md) — one pipeline, remove legacy, no fallbacks.

## Phase 9 (done — session & memory)

## Phase 8 (prod ingest)

PDF/contract-by-ID, Java sync policies — see prior summary.

## Phase 7 (done)

## Phase 6B (done)

Policy title on violations, sharper compare/routing prompts, `.env.production.example`.

## Phase 6 (done — contract only)

User sends **contract only** with `REVIEW_POLICY_SOURCE=tenant_auto` → LLM/lexical routing → discover policies from tenant index → hybrid compare.

## Phase 6 (product enablement)

Set `REVIEW_POLICY_SOURCE=tenant_auto` + `COMPLIANCE_MODE=hybrid` in production after QA.

## Phase 5 (done)

Hybrid align → prescreen → batched LLM Pass 1 → gap retrieve → Pass 2.

## Phase 4 shipped (core)

- `PgVectorDocumentStore` + SQL migration (`DOCUMENT_STORE_BACKEND=pgvector`)
- Hybrid search hook (`SEARCH_BACKEND=hybrid`, optional embeddings)
- `REVIEW_POLICY_SCOPE=request` (default) — only request-scoped policies reviewed
- Orchestrator accepts `policy_refs` / `policy_document_ids` without inline `policies[]`

## Problem (one line)

Rules live in **tenant policy documents**; review categories and retrieval are **dynamic** (Phase 1–2 done). Phase 3 LLM filter optional.

## Code facts (verified)

- Dynamic plan: `policy_plan_node` + `build_review_plan()`
- Retrieval ladder: `resolve_policy_hits()` — exact → search → catalog fetch
- Catalog: `StubPolicyCatalogClient` / `HttpPolicyCatalogClient` via `POLICY_CATALOG_URL`
- LLM filter: `filter_categories_llm()` via `REVIEW_PLAN_LLM_FILTER` (default off)

```text
load_memory → index_policies → contract_parser → clause_detection
  → policy_plan (dynamic categories)
  → policy_retrieval (get_section + search + fetch/retry)
  → compliance_review → grounding → report → save_memory
```

## Agreed defaults

| Decision | Value |
|----------|-------|
| Category granularity | One review unit per **policy parent section** |
| Max categories | 30 (`REVIEW_MAX_CATEGORIES`), warn when capped |
| `policy_refs` | Opaque `list[str]`; catalog client resolves |
| LLM category filter | Off by default (`review_plan_llm_filter=false`) |
| Policy scope | `request` (default) or `tenant` (`REVIEW_POLICY_SCOPE`) |
| Document store | `memory` (default) or `pgvector` (`DOCUMENT_STORE_BACKEND`) |
| Empty policy store | Valid report + warning, no hard fail |
| `ComplianceFinding.dimension_id` | **Keep field name**; value = `category.category_id` |
| E2E tests | Dynamic mode default; `static` opt-in via env |

## Implementation order

1. **Phase 1** — Done
2. **Phase 2** — Done
3. **Phase 3** — Done (enable `REVIEW_PLAN_LLM_FILTER=true` per tenant when needed)
4. **Phase 4** — Partial (pgvector + scope; finish 4D hardening as needed)
5. **Phase 5** — Done ([hybrid batch compliance](./PHASE5_HYBRID_COMPLIANCE_PLAN.md))
6. **Phase 6** — Done ([contract-first discovery](./PHASE6_CONTRACT_FIRST_DISCOVERY_PLAN.md))
7. **Phase 6B** — Done ([output polish](./PHASE6B_OUTPUT_POLISH_PLAN.md))
8. **Phase 7** — Done ([Java catalog integration](./PHASE7_JAVA_CATALOG_INTEGRATION_PLAN.md))
9. **Phase 9** — Done ([Postgres session & memory](../legal_ai_platform/docs/PHASE9_POSTGRES_SESSION_MEMORY_PLAN.md))
10. **Phase 10** — Planned ([Section-first + high-recall retrieval](./PHASE10_SECTION_FIRST_RETRIEVAL_PLAN.md))
