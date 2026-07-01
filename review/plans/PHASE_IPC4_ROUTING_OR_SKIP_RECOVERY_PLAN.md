# Phase IPC4 — `routing_or_skip` Recovery Plan

**Parent:** [PHASE_IPC3_PRODUCTION_EXECUTION_PLAN.md](./PHASE_IPC3_PRODUCTION_EXECUTION_PLAN.md) · [PHASE_PR01_PRECISION_FUNNEL_RECOVERY_PLAN.md](./PHASE_PR01_PRECISION_FUNNEL_RECOVERY_PLAN.md)  
**Status:** **IMPLEMENTED** (IPC4-RT1 taxonomy recovery)  
**Baseline:** Atlassian smoke run4 — `routing_or_skip=19`, all with `candidate_doc_ids=[]`

---

## 1. Root cause (confirmed from run4 audit)

| Finding | Evidence |
|---------|----------|
| **Not** low planner confidence | 14/17 skips had confidence ≥ 0.7 |
| **Not** compare LLM failure | `hit_count=0`, compare never scheduled |
| **Not** `insufficient_evidence` | Separate bucket (6); defer fix already shipped |
| **Actual cause** | Catalog semantic search + title overlap (0.15) return **zero fenced policies** → `route_decision=ipc` + empty candidates → `routing_or_skip` |

Example (`2-o5`, conf=0.8): queries ran, `candidate_doc_ids=[]`, `catalog_match_source=catalog_search`.

Policy catalog entries have **empty `topics[]`** on Atlassian tenant — topic metadata cannot help until OB-02B re-index. Recovery must use **deterministic taxonomy + keyword → policy title affinity** inside tenant fence.

---

## 2. Funnel diagram

```text
Obligation → Planner (LLM, conf 0.7–0.9)
         → Catalog search (semantic, 4 queries)
         → Title token overlap fallback (min 0.15)
         → [GAP] zero candidates → IPC routing_or_skip  ← THIS PLAN
         → Retrieval (never runs)
         → Compare LLM (never runs)
```

---

## 3. Design principle

| Layer | Role | Strictness |
|-------|------|------------|
| **Deterministic taxonomy** | Map obligation/section text → policy title hints | Loose recall, tenant-scoped |
| **Catalog search** | Semantic recall | Medium |
| **Retrieval + rerank** | Evidence quality | Medium |
| **Compare LLM** | Final precision | Strict |

**Do not** lower global `catalog_match_min_score`. Add a **recovery path only when fenced set is empty**.

---

## 4. Implementation (IPC4-RT1) — DONE

### 4.1 New module `catalog_match_recovery.py`

- Infer taxonomy categories from obligation + section + planner concepts (`_CATEGORY_KEYWORDS`)
- Map categories → policy title substrings (`_CATEGORY_POLICY_TITLE_HINTS`)
- High-precision keyword triggers (`HIPAA`, `SLA`, `AI`, `source code`, etc.)
- Score each tenant policy; pick top-K ≥ `catalog_match_taxonomy_recovery_min_score` (0.08)
- **Broad fence:** when planner confidence ≥ 0.65, allow min score 0.05

### 4.2 Wire into `catalog_matcher.py`

After title overlap fallback, if still empty → `taxonomy_recovery_candidates()` → `route_decision=expand` (via `evidence_compare_on_catalog_candidates=True`).

### 4.3 Config (defaults ON)

```
catalog_match_taxonomy_recovery_enabled=true
catalog_match_taxonomy_recovery_min_score=0.08
catalog_match_taxonomy_recovery_max_candidates=3
catalog_match_broad_fence_min_confidence=0.65
catalog_match_broad_fence_min_score=0.05
```

### 4.4 Evidence path (unchanged)

Recovery candidates route to **expand → retrieve → evidence gates → compare**. `evidence_catalog_strong_defer_enabled` still applies for low overlap + strong rerank.

---

## 5. Expected impact (Atlassian run4 targets)

| Bucket | Run4 | Target after RT1 |
|--------|------|------------------|
| `routing_or_skip` | 19 | **≤ 5** |
| `obligation_compare_count` | 19 | **≥ 28** |
| `obligation_ipc_rate` | 0.782 | **≤ 0.65** step |

Cases expected to recover: SLA→Product-Specific, HIPAA→DPA/Privacy, security→Privacy/AUP, AI→AI Terms, source code→Third-Party Code.

---

## 6. Follow-on (not in this PR)

| ID | Item | When |
|----|------|------|
| IPC4-RT2 | Raise `max_catalog_search_calls_per_review` or per-obligation fair budget | If audit shows cap starvation |
| E-BP2 | Boilerplate substantive override | After E-BP1 audit (17 skips) |
| E-EV1 | Semantic concept overlap | After RT1 smoke |
| OB-02B | Policy `topics[]` at ingest | Improves taxonomy without title hints |

---

## 7. Validation

```powershell
cd Legal\review\review_agent
python -m pytest tests/test_catalog_match_recovery.py tests/test_catalog_matcher.py -q

cd Legal\temp_java_sync
python run_pr01_atlassian_smoke.py --tenant atlassian-test-run --review-only -o outputs/atlassian_atlassian-test-run_smoke_run5.json
```

Success: `routing_or_skip` ↓, `obligation_compare_count` ↑, FUNNEL IDENTITY OK.
