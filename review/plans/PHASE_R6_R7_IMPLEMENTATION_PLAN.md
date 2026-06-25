# Phase R6 + R7 — Detailed Implementation Plan (minimal code)

**Scope:** Obligation compare + graph cutover (R6) + validation + routing audit (R7).  
**Principle:** Compare **one obligation ↔ scoped evidence** (R5 `decision=compare` only); emit IPC without LLM for the rest; attach **routing audit** on every finding. Legacy section path stays as fallback when flag off.

**Depends on:** R0–R5 (`obligations[]`, routing, catalog match, retrieval, `obligation_evidence_by_id`).  
**Estimated LOC:** ~500–650 new, ~120 touched.  
**Duration:** R6 ~1–1.5 weeks · R7 3–5 days.

---

## Root cause (why R6 + R7 exist)

R0–R5 build correct **obligation-level evidence** in state, but the **report still comes from section compare** — so Xecurify false NON_COMPLIANT can persist until cutover.

| Failure (Xecurify golden) | Root cause after R5 | R6 fix | R7 fix |
|---------------------------|---------------------|--------|--------|
| §10.1 / §10.5 still NON_COMPLIANT in report | `section_compare_llm` runs on **section retrieval** (full discovery + section categories); obligation IPC in state is **not** wired to findings | Cutover: sections with obligations → **obligation compare only**; boilerplate → IPC finding, no section compare | Audit shows `routing_or_skip`; validation blocks compare on boilerplate |
| §2.3 mixed section → wrong finding | Section compare sees **whole §2.3 text** + union of hits; cannot split security vs retention | **Per-obligation** compare with `obligation.text` span + `evidence.final_hits` only | `obligation_id` + audit on each finding |
| Duplicate / conflicting findings | Section path + obligation path would **double-compare** same section if both run | Section compare **skips** sections fully covered by obligations when flag on | Merge dedupes by `obligation_id` + section |
| Lawyer cannot explain “why Incident Response?” | No structured routing trail on findings | R6 metadata: `compliance_mode=obligation_routing` | R7 `routing_audit` blob on every finding + artifact appendix |
| Hallucinated policy ref in rationale | Compare LLM not constrained to fenced docs | Compare prompt: only `final_hits` doc IDs/titles | R7 `no_invented_policies` guard validates `policy_document_id ∈ candidates` |
| Pilot enablement risk | `OBLIGATION_ROUTING_ENABLED=true` with no report change | R6 is the **first phase that changes findings** — gated behind same flag + sub-flag | R7 CI gates before prod default flip |

**Production rule:** R5 decides *whether* to compare → R6 compares → R7 proves *why* each policy was selected → report/grounding unchanged downstream.

---

## Target flow (after R6 + R7)

```text
… evidence_sufficiency (R5)
    │
    ▼
obligation_compare (R6 NEW)     section_policy_retrieval (legacy)
    │  LLM only if decision=compare          │
    │  IPC findings deterministic            ▼
    ▼                               section_compare_llm (legacy)
obligation_merge (R6 NEW)                  │  skips obligation-covered sections when flag on
    │                                      ▼
    └──────────────► merge_section_findings (union obligation + section items)
                           ▼
                    final_gap_verify → grounding → report
                           ▲
                    R7: routing_audit on findings + artifact
```

**Flag matrix:**

| Flag | Behavior |
|------|----------|
| `OBLIGATION_ROUTING_ENABLED=false` | Identical to pre-R pipeline (zero obligation nodes cost) |
| `OBLIGATION_ROUTING_ENABLED=true`, `OBLIGATION_COMPARE_ENABLED=true` | Obligation path drives findings for obligation-covered sections |
| `OBLIGATION_ROUTING_ENABLED=true`, `OBLIGATION_COMPARE_ENABLED=false` | R0–R5 observability only (current pilot) |

**Do not** set `OBLIGATION_ROUTING_ENABLED=true` in production default until R6+R7 golden pass.

---

## R6 — Obligation compare + cutover

### Goal

For each obligation: emit `ComplianceFinding`(s) from compare LLM (when R5 `decision=compare`) or deterministic IPC (otherwise). Roll up to section/report via existing merge → grounding.

### R6.1 — Schema: `ObligationCompareItem`

**File:** `review_agent/schemas/obligation_compare.py` (~55 LOC)

```python
class ObligationCompareItem(BaseModel):
    obligation_id: str
    section_id: str
    policy_document_id: str = ""
    policy_section_id: str = ""
    dimension_label: str = ""
    status: ComplianceStatus
    severity: Severity = Severity.INFO
    contract_quote: str = ""       # substring of obligation.text preferred
    policy_quote: str = ""
    rationale: str = Field(..., min_length=5)
    confidence: float | None = None
```

**State additions** (`review_state.py`):

```python
obligation_compare_items: list[dict[str, Any]]
obligation_findings: list[dict[str, Any]]   # optional pre-merge cache
```

**Config** (`config.py` + `.env.example`):

```env
OBLIGATION_COMPARE_ENABLED=true          # sub-flag; respects OBLIGATION_ROUTING_ENABLED
OBLIGATION_COMPARE_BATCH_SIZE=4          # obligations per LLM call (same doc-set batching)
OBLIGATION_COMPARE_MAX_OBLIGATION_CHARS=2000
OBLIGATION_SECTION_CUTOVER_MODE=skip     # skip | legacy_parallel (pilot: skip)
```

---

### R6.2 — `obligation_compare_llm.py` (~180 LOC)

**Files:**

| File | LOC |
|------|-----|
| `prompts/obligation_compare.md` | adapt from `section_compare.md` |
| `services/obligation_compare_llm.py` | ~180 |

**Prompt differences (minimal):**

- Input unit = **single obligation span** (not whole section).
- Policy blocks = **`evidence.final_hits` only** (max 4 hits).
- Explicit rule: if policy topic ≠ obligation meaning → `INSUFFICIENT_POLICY_CONTEXT`.
- **At most 2 findings per obligation** (obligations are narrower than sections).

**Service API:**

```python
async def compare_obligations_batch(
    obligations: list[ContractObligation],
    evidence_by_id: dict[str, EvidenceSufficiencyResult],
    hits_by_obligation: dict[str, list[RetrievalHit]],
    *,
    contract_type: str | None,
    memory_context: str,
    settings: ReviewSettings,
    playbook_hints_by_document: dict[str, PlaybookHints] | None,
) -> tuple[list[ObligationCompareItem], list[str], dict[str, Any]]:
```

**Algorithm:**

```text
1. Partition obligations:
   - evidence.decision != "compare" → build_ipc_items() (no LLM)
   - decision == "compare" → compare queue

2. Batch compare queue:
   - Group by tuple(sorted(candidate_doc_ids)) for token efficiency
   - Reuse: filter_hits_for_compare, token_budget split, invoke_structured

3. Post-compare guards (reuse, obligation-aware):
   - apply_incorporation_guard (section text from obligation.section_id)
   - apply_equivalence_guard on ObligationCompareItem → map to SectionCompareItem shim OR extend guards to accept both

4. Quote validate against obligation.text for contract_quote (char span in section when possible)
```

**IPC item builder (deterministic, ~40 LOC in same file):**

```python
def ipc_item_from_evidence(
    obligation: ContractObligation,
    evidence: EvidenceSufficiencyResult,
    *,
    plan: ObligationRoutingPlan,
    match: CatalogMatchResult,
) -> ObligationCompareItem:
    # status=INSUFFICIENT_POLICY_CONTEXT, severity=info
    # rationale cites evidence.reason + routing confidence
```

---

### R6.3 — `obligation_merge.py` (~100 LOC)

**File:** `review_agent/services/obligation_merge.py`

```python
def obligation_items_to_findings(
    items: list[ObligationCompareItem],
    *,
    routing_audit_by_obligation: dict[str, dict[str, Any]] | None,
    hints_by_document: dict[str, PlaybookHints] | None,
    settings: ReviewSettings,
) -> list[ComplianceFinding]:
```

**Mapping rules:**

| Field | Source |
|-------|--------|
| `contract_section_id` | `item.section_id` |
| `dimension_id` | `{section_id}:{obligation_id}:{policy_section_id}` |
| `metadata.obligation_id` | `item.obligation_id` |
| `metadata.compliance_mode` | `obligation_routing` |
| `metadata.routing_audit` | R7 blob (attached in merge or validation node) |
| `metadata.source` | `obligation_compare` or `obligation_ipc` |

**Dedupe:** Reuse `dedupe_compare_items` via adapter:

```python
section_shims = [SectionCompareItem(section_id=i.section_id, ...) for i in items]
deduped, warnings = dedupe_compare_items(section_shims, ...)
```

**Do not** duplicate `finding_dedupe.py` logic.

---

### R6.4 — Graph nodes + cutover

**File:** `review_agent/graph/obligation_compare_nodes.py` (~90 LOC)

```python
async def obligation_compare_node(state, client) -> dict:
    if not settings.obligation_routing_enabled or not settings.obligation_compare_enabled:
        return {}

    # Load obligations, evidence_by_id, plans, matches, retrieval bundles
    items = []
    items.extend(ipc_items_for_non_compare(...))
    compare_items, warnings, stats = await compare_obligations_batch(...)
    items.extend(compare_items)

    findings = obligation_items_to_findings(items, routing_audit_by_obligation=...)

    return {
        "obligation_compare_items": [i.model_dump(mode="json") for i in items],
        "obligation_findings": [f.model_dump(mode="json") for f in findings],
        "compliance_stats": {..., "obligation_compare_count": len(compare_items), "obligation_ipc_count": ...},
    }
```

**Graph wire** (`review_graph.py`):

```text
evidence_sufficiency → obligation_compare → section_policy_retrieval → section_compare_llm → merge
```

**Section compare cutover** (`section_compare_nodes.py`, ~25 LOC):

```python
def _sections_for_legacy_compare(state, settings) -> list[IndexedChunk]:
    if not settings.obligation_routing_enabled or not settings.obligation_compare_enabled:
        return all_sections
    obligation_section_ids = {ob.section_id for ob in obligations}
  if settings.obligation_section_cutover_mode == "skip":
        return [s for s in sections if s.section_id not in obligation_section_ids]
    return all_sections  # legacy_parallel for A/B only
```

Apply filter in `section_compare_llm_node` before `compare_all_sections`.

**Merge union** (`merge_section_findings_node`, ~20 LOC):

```python
obligation_findings = [ComplianceFinding.model_validate(f) for f in state.get("obligation_findings") or []]
merged = merge_section_findings(section_items, ...)
all_findings = obligation_findings + merged.findings
# dedupe cross-path: same section_id + policy_document_id + status → keep obligation_routing source
```

---

### R6.5 — Tests

| Test | Assert |
|------|--------|
| `test_obligation_ipc_no_llm` | `decision=ipc` → IPC item, `invoke_structured` not called |
| `test_obligation_compare_happy` | Mock LLM → finding with `metadata.obligation_id` |
| `test_section_cutover_skips` | §2.3 with obligations → not in `compare_all_sections` input |
| `test_boilerplate_report_ipc` | §10.1 → report finding status IPC only |
| `test_flag_off_unchanged` | Section e2e identical when routing off |
| `test_merge_dedupe` | Two obligations same section → distinct findings |

**Golden extension** (`routing_golden.json`):

```json
{"obligation_id":"10.1-o0","expect_finding_status":"INSUFFICIENT_POLICY_CONTEXT"}
{"obligation_id":"2.3-o0","expect_policy_title_contains":"Security Practices"}
```

---

### R6 done when

- [ ] Xecurify end-to-end with `OBLIGATION_ROUTING_ENABLED=true` + `OBLIGATION_COMPARE_ENABLED=true`
- [ ] §10.1 / §10.5 → IPC in **report** (not NON_COMPLIANT)
- [ ] §2.3 security obligation → Security Practices policy in finding
- [ ] Weighted alignment ≥70 on pilot (target; baseline ~57)
- [ ] Flag off → acme/section e2e unchanged

---

## R7 — Validation + routing audit

### Goal

Thin **tenant-agnostic** guards on routing outputs; **lawyer-facing explainability** via audit JSON on every obligation-sourced finding.

### R7.1 — `routing_audit.py` (~90 LOC)

**File:** `review_agent/services/routing_audit.py`

```python
@dataclass(frozen=True)
class ObligationRoutingAudit:
    obligation_id: str
    routing_source: str
    routing_confidence: float
    candidate_doc_ids: list[str]
    candidate_scores: dict[str, float]
    rejected: list[dict[str, str]]
    queries_used: list[str]
    catalog_match_source: str
    evidence_decision: str
    evidence_reason: str
    hit_count: int
    expand_round: int

def build_routing_audit(
    *,
    obligation_id: str,
    plan: ObligationRoutingPlan,
    match: CatalogMatchResult,
    bundle: ObligationRetrievalBundle | None,
    evidence: EvidenceSufficiencyResult | None,
    indexed_policies: list[dict],
) -> dict[str, Any]:
```

**Resolve titles** from `indexed_policies` for `candidate_doc_titles[]` in audit (no extra registry call).

---

### R7.2 — `routing_validation.py` (~80 LOC)

**File:** `review_agent/services/routing_validation.py`

| Guard | Check | On fail |
|-------|-------|---------|
| `tenant_doc_exists` | every `candidate_doc_id` ∈ indexed policy set | drop doc + `rejected` reason |
| `no_invented_policies` | compare item `policy_document_id` ∈ candidates ∪ hit doc ids | downgrade to IPC |
| `boilerplate_ipc` | `obligation.is_boilerplate` → no compare item with NON_COMPLIANT | force IPC |
| `permission` | all audit `tenant_id` matches state (defensive) | log warning |

```python
def validate_obligation_compare_items(
    items: list[ObligationCompareItem],
    *,
    obligations_by_id: dict[str, ContractObligation],
    audit_by_id: dict[str, dict],
    allowed_doc_ids: set[str],
) -> tuple[list[ObligationCompareItem], list[str]]:
```

**Run in** `obligation_compare_node` **after** LLM, before merge — not a separate graph node (minimal).

---

### R7.3 — Artifact + finding metadata

**Files touched:**

| File | Change |
|------|--------|
| `schemas/review_artifact.py` | +`ObligationRoutingAuditRow`, +`obligation_routing: list[...]` |
| `services/review_artifact.py` | Build obligation rows from state when present |
| `services/obligation_merge.py` | Attach `metadata.routing_audit` |

```python
class ObligationRoutingAuditRow(BaseModel):
    obligation_id: str
    section_id: str
    routing_source: str = ""
    confidence: float = 0.0
    candidate_doc_ids: list[str] = Field(default_factory=list)
    candidate_titles: list[str] = Field(default_factory=list)
    evidence_decision: str = ""
    evidence_reason: str = ""
    queries_used: list[str] = Field(default_factory=list)
    hit_count: int = 0
```

**Artifact version:** bump to `1.1` when `obligation_routing` non-empty.

**Report (optional, minimal):** If `metadata.routing_audit` present, `reports/generator.py` adds one line under rationale: *Policy selected via: {routing_source} (confidence {confidence}).*

---

### R7.4 — Grounding extension (~25 LOC)

**File:** `graph/nodes.py` — `grounding_node`

When `metadata.obligation_id` set:

- Prefer `verify_quote` with `section_id` + optional char offset from obligation (`char_start`/`char_end` in audit if stored).
- IPC findings: keep `grounded=True` (existing behavior).

No new MCP endpoints.

---

### R7.5 — Stats / ops counters

Extend `compliance_stats`:

```json
{
  "obligation_compare_llm_calls": 3,
  "obligation_ipc_findings": 4,
  "routing_validation_rejected": 0,
  "wrong_policy_compare_blocked": 0
}
```

---

### R7.6 — Tests

| Test | Assert |
|------|--------|
| `test_audit_blob_complete` | All R2–R5 fields present in `build_routing_audit` |
| `test_validate_invented_policy_blocked` | LLM item with foreign `policy_document_id` → IPC |
| `test_boilerplate_validation` | boilerplate obligation cannot emit NON_COMPLIANT |
| `test_artifact_obligation_rows` | `build_review_artifact` includes `obligation_routing` |
| `test_finding_has_routing_audit` | Every obligation finding has `metadata.routing_audit` |
| `test_golden_no_wrong_policy` | §10.1/§10.5 findings never cite Incident Response doc id |

---

### R7 done when

- [ ] 100% obligation findings have `routing_audit` JSON
- [ ] Validation rejects out-of-fence `policy_document_id` (unit + golden)
- [ ] Artifact export includes obligation routing appendix
- [ ] CI gate: `wrong_policy_compare_count == 0` on golden set

---

## File change matrix

| File | Phase | Change |
|------|-------|--------|
| `schemas/obligation_compare.py` | R6 | **new** |
| `prompts/obligation_compare.md` | R6 | **new** |
| `services/obligation_compare_llm.py` | R6 | **new** |
| `services/obligation_merge.py` | R6 | **new** |
| `graph/obligation_compare_nodes.py` | R6 | **new** |
| `graph/section_compare_nodes.py` | R6 | ~25 LOC section skip + merge union |
| `graph/review_graph.py` | R6 | +1 node, +1 edge |
| `state/review_state.py` | R6 | +2 fields |
| `config.py`, `.env.example` | R6+R7 | +6 settings |
| `services/routing_audit.py` | R7 | **new** |
| `services/routing_validation.py` | R7 | **new** |
| `schemas/review_artifact.py` | R7 | +ObligationRoutingAuditRow |
| `services/review_artifact.py` | R7 | ~40 LOC |
| `services/obligation_merge.py` | R7 | audit attach |
| `graph/nodes.py` | R7 | ~25 LOC grounding |
| `reports/generator.py` | R7 | optional 1-line audit hint |
| `tests/test_obligation_compare.py` | R6 | **new** |
| `tests/test_routing_audit.py` | R7 | **new** |
| `tests/test_routing_validation.py` | R7 | **new** |
| `tests/fixtures/routing_golden.json` | R6+R7 | extend |

**Do NOT touch in R6/R7:** `multi_retrieval.py`, `catalog_matcher.py`, `semantic_routing_planner.py`, `named_policy_routing.py` (remove in R9), `policy_discovery` core logic.

---

## Graph diff (exact)

```python
# review_graph.py
from review_agent.graph.obligation_compare_nodes import obligation_compare_node

_add_timed_node(graph, "obligation_compare", obligation_compare_node, client=client)

graph.add_edge("evidence_sufficiency", "obligation_compare")
graph.add_edge("obligation_compare", "section_policy_retrieval")
# remove: evidence_sufficiency → section_policy_retrieval direct edge
```

**Initial state** (+2 keys):

```python
"obligation_compare_items": [],
"obligation_findings": [],
```

---

## Interface contract (R6 → downstream)

```python
# merge_section_findings_node
obligation_findings: list[ComplianceFinding]  # from state
section_findings = merge_section_findings(section_items, ...)
findings = _merge_obligation_and_section(obligation_findings, section_findings)

# grounding / report — unchanged; findings carry metadata.routing_audit
```

---

## Execution order

### R6 (days 1–8)

| Day | Tasks |
|-----|-------|
| 1 | `obligation_compare.py` schema + IPC builder |
| 2 | `obligation_compare.md` + `compare_obligations_batch` |
| 3 | Guards + quote validate adapter |
| 4 | `obligation_merge.py` + `obligation_compare_node` |
| 5 | Section cutover filter + merge union |
| 6 | Graph wire + flag tests |
| 7 | Golden fixture finding expectations |
| 8 | Xecurify pilot run + alignment check |

### R7 (days 9–12)

| Day | Tasks |
|-----|-------|
| 9 | `routing_audit.py` + merge attach |
| 10 | `routing_validation.py` + wire in compare node |
| 11 | Artifact + optional report line |
| 12 | Golden + CI gates + grounding char span |

---

## What NOT to do

| Anti-pattern | Why |
|--------------|-----|
| Remove section path in R6 | Blast radius; keep fallback until R9 |
| Flip `OBLIGATION_ROUTING_ENABLED` default to true in R6 | Opt-in pilot only |
| New taxonomy / tenant rules in validation | R7 guards are universal only |
| LLM routing audit narrative | Structured JSON from state, not generated prose |
| Compare boilerplate obligations | IPC deterministic only |
| Per-finding registry fetch | Use `indexed_policies` already in state |

---

## Success metrics (CI gates)

| Gate | Target |
|------|--------|
| Golden wrong-policy finding | **0** (§10.1, §10.5 never IR) |
| Obligation finding audit coverage | **100%** |
| `policy_document_id ∉ candidates` in report | **0** |
| Flag off regression | section e2e unchanged |
| Xecurify weighted alignment (pilot) | **≥70** (stretch from ~57) |

---

## Ops prerequisites

Before enabling compare cutover on Xecurify:

1. R0–R5 validated (`obligation_evidence_by_id` populated correctly)
2. `OBLIGATION_COMPARE_ENABLED=true` on pilot tenant only
3. Re-run assessment; diff against `temp_java_sync/outputs/xecurify_nda_assessment.json`
4. Manual review of `routing_audit` on 3 sample findings

**Production default flip** (all tenants): after R8 golden CI green — not in R6/R7 scope.

---

## Immediate first PR (R6 core, mergeable)

1. `obligation_compare.py` + IPC builder + tests  
2. `obligation_compare_llm.py` + prompt (no graph cutover)  
3. `obligation_merge.py`  

PR 2: graph node + section skip + merge union  
PR 3: R7 audit + validation + artifact
