# Phase 3 — Prompt Split & LLM Category Filter Plan

**Plan ID:** `DR-PHASE-3`  
**Status:** Deferred — implement only after Phase 1 + 2 are green  
**Prerequisite:** Phase 1 (dynamic categories), Phase 2 (retrieval ladder)  
**Default:** `review_plan_llm_filter=false`

---

## 1. Executive summary

Add an **optional LLM step** to **filter/rank** pre-built `review_categories` (from Phase 1) against the contract — never to invent new categories. Keep **`compliance_review.md` compare-only**. This reduces LLM cost on large playbooks (80+ sections) without changing compliance rules source (still policy text only).

**Principle:** Smallest prompt surface; fail-open on LLM errors; strict JSON schema; no new graph node — hook inside `build_review_plan()` behind a flag.

---

## 2. Root cause

| Symptom | Root cause |
|---------|------------|
| Large enterprise playbook → 80+ LLM compare calls | Phase 1 reviews every policy section |
| Putting retrieval in compare prompt | Wrong layer — LLM cannot call tools |
| LLM defining all categories | Risk of topics not in tenant policies (rejected in Phase 1) |

Phase 3 solves **cost/coverage tuning**, not **correctness**. Correctness comes from Phase 1 (what exists in policies) + Phase 2 (fetch/retry).

---

## 3. Solution strategy

### Two prompts, two jobs

| Prompt | Job | Input | Output |
|--------|-----|-------|--------|
| `policy_plan.md` | Filter/rank existing categories | Contract section titles + categories JSON | Subset of category IDs + optional query overrides |
| `compliance_review.md` | Compare retrieved sections | Policy section text + contract section text | `ComplianceLLMResult` (unchanged) |

### Fail-open policy

If LLM filter fails (parse error, timeout, invalid IDs) → **return all categories** from Phase 1. Coverage beats silent under-review.

### When to enable

```env
REVIEW_PLAN_LLM_FILTER=true
```

Only for tenants with `review_max_categories` frequently hit or playbooks > 30 sections.

---

## 4. Detailed subtasks

### 4.1 Schema — `PolicyPlanFilterResult`

**File (new):** `review_agent/schemas/policy_plan_llm.py`  
**Est. lines:** ~25  

```python
class PolicyPlanFilterResult(BaseModel):
    relevant_category_ids: list[str] = Field(..., min_length=0)
    search_query_overrides: dict[str, list[str]] = Field(default_factory=dict)
    rationale: str = ""  # optional audit trail, not shown to end user by default
```

**Validation (post-LLM):**

```python
valid_ids = {c.category_id for c in categories}
filtered_ids = [i for i in result.relevant_category_ids if i in valid_ids]
if not filtered_ids and categories:
    return categories  # fail-open: empty filter = use all
```

**Acceptance:** Unknown IDs stripped; empty result with non-empty input → fail-open to all.

---

### 4.2 Prompt — `policy_plan.md`

**File (new):** `review_agent/prompts/policy_plan.md`  
**Est. lines:** ~45  

**## SYSTEM (binding rules):**

1. You receive a contract summary (section titles) and a **closed list** of policy review categories (pre-derived from company policy documents).
2. Return only `relevant_category_ids` that appear in the input list — **do not add new IDs**.
3. Do not state compliance verdicts (COMPLIANT/NON_COMPLIANT).
4. Do not invent policy requirements.
5. If uncertain whether a category applies, **include** it (prefer coverage).
6. `search_query_overrides` optional — better search phrases for contract retrieval only.
7. Respond with structured JSON only.

**## USER template:**

```
Contract type: {contract_type}
Contract section titles:
{contract_section_titles}

Policy review categories (pre-derived from indexed policies):
{categories_json}

Return which category IDs are relevant to reviewing this contract.
```

**categories_json** shape (minimal):

```json
[{"id": "uuid:4", "label": "4. Limitation of Liability", "policy_title": "Vendor Policy"}]
```

---

### 4.3 Service — `filter_categories_llm()`

**File (new):** `review_agent/services/policy_plan_llm.py`  
**Est. lines:** ~70  

```python
async def filter_categories_llm(
    *,
    categories: list[ReviewCategory],
    contract_sections: list[IndexedChunk],
    contract_type: str | None,
    settings: ReviewSettings,
) -> list[ReviewCategory]:
```

**Flow:**

1. If not `settings.review_plan_llm_filter` → return categories unchanged.
2. If `len(categories) <= settings.review_plan_llm_filter_min_categories` (default 15) → skip LLM.
3. Load `policy_plan.md` (same `## SYSTEM` / `## USER` split as compliance prompt).
4. `invoke_structured(model, PolicyPlanFilterResult, ...)`.
5. Validate IDs; apply `search_query_overrides` to matching `ReviewCategory.search_queries`.
6. Return filtered list preserving original order.

**Reuse:** `review_agent/models/llm_gateway.py` — `get_review_model`, `invoke_structured`.

**Config:**

| Setting | Default |
|---------|---------|
| `review_plan_llm_filter` | `false` |
| `review_plan_llm_filter_min_categories` | `15` |
| `review_plan_llm_temperature` | `0.0` |
| `review_plan_llm_max_retries` | `1` |

---

### 4.4 Integrate into `build_review_plan()`

**File:** `review_agent/services/policy_plan.py`  
**Est. change:** ~15 lines  

At end of `build_review_plan()`:

```python
if settings.review_plan_llm_filter and categories:
    categories = await filter_categories_llm(
        categories=categories,
        contract_sections=contract_sections,  # pass from state via policy_plan_node
        contract_type=contract_type,
        settings=settings,
    )
```

**`policy_plan_node` change:** Pass `state["contract_sections"]` into `build_review_plan()`.

**No new graph node.**

---

### 4.5 Update `compliance_review.md` (one line)

**File:** `review_agent/prompts/compliance_review.md`  

Add under SYSTEM rule 1:

> The Policy and Contract sections below were pre-selected by retrieval. Do not assume other policy text exists beyond what is provided.

**Do not** add fetch, plan, or catalog instructions.

---

### 4.6 Tests

**File (new):** `tests/test_policy_plan_llm.py` (~80 lines)

| ID | Test | Pass criteria |
|----|------|---------------|
| P3-T1 | Filter disabled | All categories returned, no LLM call |
| P3-T2 | Below min threshold | Skip LLM even if filter enabled |
| P3-T3 | Mock LLM returns subset | Only valid IDs kept |
| P3-T4 | Mock returns unknown ID | Unknown stripped |
| P3-T5 | Mock returns `[]` | Fail-open → all categories |
| P3-T6 | LLM raises | Fail-open → all categories |
| P3-T7 | Query override applied | Category search_queries updated |

**Mock pattern:** Same as `test_compliance_llm.py` — monkeypatch `invoke_structured`.

---

## 5. File change summary

| File | Action | ~Lines |
|------|--------|-------:|
| `schemas/policy_plan_llm.py` | New | 25 |
| `prompts/policy_plan.md` | New | 45 |
| `services/policy_plan_llm.py` | New | 70 |
| `services/policy_plan.py` | Modify | 15 |
| `graph/nodes.py` | Pass contract_sections | 5 |
| `config.py` | Extend | 10 |
| `.env.example` | Extend | 6 |
| `prompts/compliance_review.md` | +1 line | 1 |
| `tests/test_policy_plan_llm.py` | New | 80 |

**Total Phase 3:** ~120 production + ~80 test

---

## 6. Cost / latency model

| Playbook sections | Phase 1 only | Phase 3 filter + Phase 1 |
|-------------------|-------------|--------------------------|
| 10 sections | 10 compare LLM calls | 1 filter + ~6 compare (example) |
| 50 sections | 50 compare calls | 1 filter + ~15 compare |
| 80 sections (capped 30) | 30 compare | 1 filter + ~20 compare |

Filter LLM input tokens ≈ section titles + category labels only — small vs full section text in compare.

---

## 7. Risks and mitigations

| Risk | Mitigation |
|------|------------|
| LLM drops important category | Fail-open on empty; prompt says "when uncertain, include" |
| LLM adds fake category ID | Schema validation strips unknown IDs |
| Extra latency | Skip filter when categories ≤ 15 |
| Two LLM calls per review | Filter only when enabled + large playbook |

---

## 8. Definition of done

- [ ] `REVIEW_PLAN_LLM_FILTER=false` by default
- [ ] Filter never adds categories not in Phase 1 output
- [ ] LLM failure → all categories retained
- [ ] `compliance_review.md` remains compare-only
- [ ] Tests mock LLM; no live API key required in CI
- [ ] Documented in `review/README.md` and `plans/README.md`

---

## 9. Out of scope (Phase 3)

- LLM-generated search queries without category list (use overrides only)
- Merging multiple sections into one category
- Contract type LLM classifier (separate future task)
- Replacing Phase 1 section enumeration with LLM plan

---

## 10. Go / no-go

| Gate | Requirement |
|------|-------------|
| **GO** | Phase 1 + 2 tests green; production traffic shows cap warnings |
| **NO-GO** | Phase 1 not shipped; or category count always < 15 |

**Recommendation:** Ship Phase 1 + 2 first. Enable Phase 3 per-tenant when cost metrics justify it.
