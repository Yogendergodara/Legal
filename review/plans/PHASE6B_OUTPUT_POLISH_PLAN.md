# Phase 6B — Output Polish & Production Defaults

**Plan ID:** `DR-PHASE-6B`  
**Status:** Implemented  
**Prerequisite:** Phase 6 contract-first discovery (core)  
**Principle:** Minimal diff — enrich existing findings/report/prompts; no new graph nodes  

---

## 1. Executive summary

Four small production tasks close gaps between **what the system knows** (policy document IDs, titles, contract type) and **what the user sees** (which playbook was violated, sharper LLM compare, better discovery topics, product env defaults).

| Task | Root problem | Fix (minimal) |
|------|--------------|---------------|
| **6B-1** Policy name on violations | `policy_document_id` stored; `title` never copied to findings/report | One enrich helper + report line |
| **6B-2** Sharpen match prompts | Prompts generic; batch items omit playbook name + contract type | Edit 2 `.md` files + 1 format function |
| **6B-3** Routing prompt tune | Free-form topics miss indexed section titles → low search recall | Topic hints + optional tenant section seed |
| **6B-4** Product defaults | Dev defaults (`request` + `llm`) not product path | Documented prod `.env` + QA gate |

**Estimated new code:** ~120–180 lines. **No pipeline rewrite.**

---

## 2. Current state vs desired (gaps)

### 2.1 What works today

```text
Finding fields:  dimension_label, policy_document_id, policy_quote, contract_quote, status
State:           indexed_policies[{document_id, title, ...}], discovered_policies[...]
Report:          "### {dimension_label} — NON_COMPLIANT" + quotes
Grounding:       Substring verify on stored docs (no LLM)
```

### 2.2 Gaps (bugs / UX holes)

| ID | Gap | Symptom | Severity |
|----|-----|---------|----------|
| **G1** | No `policy_title` on findings | Lawyer sees "Limitation of Liability" but not **which playbook** (e.g. "Vendor MSA Playbook 2024") | High — user-requested |
| **G2** | Batch LLM items lack playbook name | Model compares sections without document context; rationale rarely names source playbook | Medium |
| **G3** | `contract_type` not in compare prompts | MSA vs NDA nuance ignored at match time | Medium |
| **G4** | Routing topics free-form | Discovery search scores ~0.08–0.13; wrong topics → empty discovery | Medium |
| **G5** | Prod still `REVIEW_POLICY_SOURCE=request` | Contract-only path not default; extra LLM calls (`COMPLIANCE_MODE=llm`) | Config / ops |
| **G6** | Prescreen + lexical findings lack title | Same as G1 for non-LLM paths | Medium |
| **G7** | API `artifacts.report.findings` has no `policy_title` | Downstream UI cannot show violated policy without extra lookup | Medium |

### 2.3 Root cause (one line each)

- **G1/G6/G7:** Title exists in `state.indexed_policies` at graph end but **no step copies it into `ComplianceFinding.metadata`**.
- **G2/G3:** Prompts and `_format_batch_items()` were built for generic compare, not tenant playbook product.
- **G4:** Routing optimized for LLM topic quality, not **lexical search alignment** with indexed section titles.
- **G5:** Intentional dev-safe defaults; product flip needs QA checklist.

---

## 3. Task 6B-1 — Policy name on violations

### 3.1 Problem

User question: *"Which policy is violated?"*

Today:
- `ComplianceFinding.policy_document_id` → UUID only
- `dimension_label` → policy **section** title (e.g. "4. Limitation of Liability")
- Report (`reports/generator.py`) prints section label, not playbook document name

Data is available in `state.indexed_policies` and `state.discovered_policies` but **never joined** to findings.

### 3.2 Production solution (minimal)

**Single enrich step** before grounding (one place, all compliance modes).

#### 6B-1.1 — Helper `services/finding_enrich.py` (new, ~35 lines)

```python
def build_policy_title_map(
    indexed_policies: list[dict],
    discovered_policies: list[dict] | None = None,
) -> dict[str, str]:
    """document_id (str) -> title."""

def enrich_findings_policy_titles(
    findings: list[ComplianceFinding],
    title_map: dict[str, str],
) -> list[ComplianceFinding]:
    """Set metadata['policy_title'] when policy_document_id known."""
```

Rules:
- Key = `str(finding.policy_document_id)`
- If title missing in map → `metadata["policy_title"] = ""` (do not invent)
- Idempotent: do not overwrite non-empty `policy_title`

#### 6B-1.2 — Wire in `grounding_node` (or new thin node before it)

**File:** `graph/nodes.py` — `grounding_node` start:

```python
title_map = build_policy_title_map(
    state.get("indexed_policies") or [],
    state.get("discovered_policies"),
)
findings = enrich_findings_policy_titles(state.get("findings") or [], title_map)
# then existing grounding loop on `findings`
```

**Why grounding_node:** All paths (`llm`, `lexical`, `hybrid`) converge on `findings` → `grounding` → `report`. One hook covers G1 + G6.

**Alternative (even smaller):** enrich in `report_node` only — fixes report but not API artifacts mid-pipeline. **Prefer grounding_node** for API consistency.

#### 6B-1.3 — Report display

**File:** `reports/generator.py` — `_finding_block()`:

```markdown
### Limitation of Liability — `NON_COMPLIANT`
- **Violated policy:** Vendor Playbook 2024
- **Policy document:** `{policy_document_id}`
- **Severity:** ...
```

Show **Violated policy** line when:
- `status == NON_COMPLIANT` (or always if title present — product choice: **always if title present**)

Read from `finding.metadata.get("policy_title")` with fallback to `—`.

#### 6B-1.4 — Optional: first-class field (defer)

Do **not** add `policy_title` to `ComplianceFinding` schema yet — `metadata` keeps diff minimal. Phase 7 can promote to top-level field if UI needs it.

### 3.3 Files touched

| File | Change |
|------|--------|
| `services/finding_enrich.py` | **NEW** |
| `graph/nodes.py` | ~8 lines in `grounding_node` |
| `reports/generator.py` | ~4 lines in `_finding_block` |
| `tests/test_finding_enrich.py` | **NEW** |
| `tests/test_review_e2e.py` | Assert `policy_title` in report for NON_COMPLIANT |

### 3.4 Acceptance criteria

- [ ] NON_COMPLIANT finding shows `Violated policy: {title}` in markdown report
- [ ] `artifacts.report.findings[].metadata.policy_title` populated when `indexed_policies` has title
- [ ] Works for `request`, `tenant_auto`, hybrid, and lexical modes
- [ ] No title → line omitted or `—`; no crash
- [ ] Existing 47 tests still pass

---

## 4. Task 6B-2 — Sharpen match prompts

### 4.1 Problem

Match prompts (`compliance_review.md`, `compliance_review_batch.md`) are **legally correct** but **product-generic**:

| Issue | Effect |
|-------|--------|
| No "company playbook" framing | Model may apply general law |
| No `contract_type` in USER block | Weak MSA/NDA/SOW context |
| Batch items: Label only, no playbook name | Rationale doesn't cite which policy doc |
| NON_COMPLIANT definition implicit | Inconsistent severity / status |

Pre-grounding in `compliance_llm.py` already downgrades bad quotes — prompts should reduce that waste.

### 4.2 Production solution (minimal)

**Prompt-only + small template variables** — no schema change to `ComplianceLLMResult`.

#### 6B-2.1 — Update `prompts/compliance_review.md`

Add to SYSTEM:
- "You review **in-house company playbook** text against a **customer/vendor contract**."
- "Contract type context: `{contract_type}` — interpret playbook requirements in that commercial context only."
- NON_COMPLIANT: "Contract text **fails to meet** an explicit requirement stated in the policy section."
- Rationale must start with: `Policy section "{dimension_label}"` and state gap in one sentence.

Add to USER:
```markdown
- **Contract type:** {contract_type}
- **Playbook document:** {policy_title}
```

#### 6B-2.2 — Update `prompts/compliance_review_batch.md`

Add to SYSTEM (same playbook framing + NON_COMPLIANT rule).

Add rationale rule: *"For NON_COMPLIANT, name the policy section and quote the conflicting requirement."*

#### 6B-2.3 — Code: pass variables into prompts

**File:** `services/compliance_llm.py` — `compare_sections_llm()`:

- Add params: `contract_type: str | None`, `policy_title: str`
- Format USER template with new placeholders (default `unknown` / `Company Playbook`)

**File:** `services/compliance_batch_llm.py`:

- `_format_batch_items()`: add line `- **Playbook:** {title}` per item from `policy_titles_by_doc.get(str(category.policy_document_id), "")`
- `compare_batch_llm()` / `run_batched_compliance()`: add `policy_titles_by_doc: dict[str, str]` param
- **File:** `graph/hybrid_nodes.py` + `graph/nodes.py` (`compliance_review_node`): build map from `state.indexed_policies` once, pass down

```python
def build_policy_title_map(indexed_policies): ...  # reuse from 6B-1
```

**File:** `graph/nodes.py` `compliance_review_node`: pass title map into `compare_sections_llm`.

#### 6B-2.4 — Prompt text (production draft snippets)

**SYSTEM addition (both prompts):**

```markdown
**Domain context:**
- You compare a **tenant company playbook** (policy) against an **agreement under review** (contract).
- Contract type: commercial agreements (MSA, NDA, SOW, etc.). Use supplied `contract_type` only as context — do not import external law.
- `NON_COMPLIANT` = the contract text **does not satisfy** an explicit, quoted requirement in the policy section.
- `COMPLIANT` = the contract text **meets** the policy requirement as written (not stricter than policy).
- In `rationale`, name the **policy section** and explain the gap or alignment in plain language.
```

### 4.3 Files touched

| File | Change |
|------|--------|
| `prompts/compliance_review.md` | Rewrite SYSTEM/USER blocks (~15 lines) |
| `prompts/compliance_review_batch.md` | Same framing (~10 lines) |
| `services/compliance_llm.py` | Template vars (~15 lines) |
| `services/compliance_batch_llm.py` | Playbook in batch items + param (~20 lines) |
| `graph/nodes.py` | Pass title map to llm node |
| `graph/hybrid_nodes.py` | Pass title map to pass1/pass2 |
| `tests/test_compliance_llm.py` | Assert template includes policy_title when mocked |

### 4.4 Risks & mitigations

| Risk | Mitigation |
|------|------------|
| Longer prompts → more tokens | Playbook name is one line; contract_type one line |
| Breaking prompt parse (`## SYSTEM` / `## USER`) | Keep markers; run existing prompt load tests |
| LLM ignores new rules | Pre-grounding + quote rules unchanged |

### 4.5 Acceptance criteria

- [ ] Batch item block includes `Playbook: {title}` when title known
- [ ] Single compare USER includes `contract_type` and `policy_title`
- [ ] No change to `ComplianceFinding` schema or grounding logic
- [ ] CI tests pass (`COMPLIANCE_MODE=lexical` unchanged)

---

## 5. Task 6B-3 — Routing prompt tune (discovery recall)

### 5.1 Problem

**Root:** Routing produces **semantic** topics; discovery uses **lexical BM25** search. Mismatch → low scores or zero hits.

Observed: query `"limitation of liability"` → score **~0.127** (below old 0.15 threshold; fixed to 0.08 in Phase 6).

Remaining gaps:
- Topics like `"liability cap"` may not match section title `"4. Limitation of Liability"`
- No seed from tenant's **actual indexed section titles**
- `contract_routing.md` lists themes but not **search-optimized phrases**

### 5.2 Production solution (two layers, minimal)

#### Layer A — Prompt + static hints (no new API)

**6B-3.1** — Extend `prompts/contract_routing.md`:

Add to SYSTEM rule 8:
```markdown
8. Prefer **search-optimized phrases** that match typical playbook section headings, e.g.:
   `limitation of liability`, `indemnification`, `confidentiality`, `termination`,
   `data protection`, `governing law`, `intellectual property`, `warranties`, `assignment`.
   Avoid vague topics like "legal stuff" or "general terms".
```

**6B-3.2** — New optional file `prompts/routing_topic_hints.yaml` (or reuse `review_dimensions.yaml`):

```yaml
# Canonical discovery topics — aligned with lexical search + static dimensions
topics:
  - limitation of liability
  - indemnification
  - confidentiality
  - termination
  - data protection
  - governing law
```

**6B-3.3** — `services/contract_routing.py`:

- `load_routing_topic_hints() -> list[str]` from yaml (fallback to hardcoded list)
- Append to USER block: `Suggested topic vocabulary (use where applicable): {hints}`

~25 lines. No new env var required (optional `ROUTING_TOPIC_HINTS_PATH` later).

#### Layer B — Tenant section title seed (tenant_auto only, high value)

**6B-3.4** — `services/contract_routing.py` — optional enrich before LLM:

When `review_policy_source=tenant_auto`:
1. `list_policies(tenant_id)` → for each doc (cap 5), `list_sections` → collect parent `title`s
2. Pass top 20 unique titles into USER: `Indexed playbook sections in tenant (prefer matching topics): ...`

**Why safe:** Read-only; does not widen review scope — only improves routing topics for search.

**Skip when:** `list_policies` empty (no indexed playbooks yet) — fall back to Layer A only.

#### Layer C — Lexical routing alignment (already partial)

`route_contract_lexical()` uses keyword map — extend `_TOPIC_KEYWORDS` to mirror `routing_topic_hints.yaml` (single source).

### 5.3 Files touched

| File | Change |
|------|--------|
| `prompts/contract_routing.md` | Search-optimized topic rules |
| `prompts/routing_topic_hints.yaml` | **NEW** (or import from dimensions) |
| `services/contract_routing.py` | Load hints + optional tenant section seed (~40 lines) |
| `graph/discovery_nodes.py` | Pass `client` into routing for tenant section seed (if not already) |
| `tests/test_contract_routing.py` | Hints appear in context; lexical topics match yaml |

### 5.4 Acceptance criteria

- [ ] Routing USER block includes topic hint list
- [ ] `tenant_auto` + pre-indexed policies: routing context includes section titles from store
- [ ] Discovery hit rate on SAMPLE_CONTRACT + SAMPLE_POLICY fixture ≥ current (no regression)
- [ ] `request` path unchanged when `tenant_auto` off

---

## 6. Task 6B-4 — Product defaults

### 6.1 Problem

| Setting | Dev default | Product need |
|---------|-------------|--------------|
| `REVIEW_POLICY_SOURCE` | `request` | `tenant_auto` (contract only) |
| `COMPLIANCE_MODE` | `llm` | `hybrid` (fewer calls, prescreen) |
| `REVIEW_POLICY_SCOPE` | `request` | `discovered` (auto under tenant_auto) |

Flipping without QA risks breaking inline-policy clients and CI (lexical tests).

### 6.2 Production solution

#### 6B-4.1 — Split env files (no code default change)

| File | Purpose |
|------|---------|
| `.env.example` | Documents all vars; **keeps dev-safe defaults** |
| `.env.production.example` | **NEW** — product values |

`.env.production.example`:
```env
REVIEW_POLICY_SOURCE=tenant_auto
COMPLIANCE_MODE=hybrid
REVIEW_POLICY_SCOPE=discovered
CONTRACT_ROUTING_MODE=llm
DOCUMENT_STORE_BACKEND=pgvector
# DATABASE_URL=...
```

**Do not** change `config.py` defaults — CI and local dev stay stable.

#### 6B-4.2 — QA gate checklist (document in plan + README)

Before prod flip:

```text
[ ] Policies indexed for pilot tenant(s) in pgvector
[ ] POST /query contract-only → discoveries ≥ 1 policy
[ ] Report shows NON_COMPLIANT with policy_title (6B-1)
[ ] Grounding warnings acceptable rate (< X% dropped) 
[ ] Inline policies[] path still works (REVIEW_POLICY_SOURCE=request)
[ ] Hybrid pass2 gap path tested with missing section
[ ] Latency budget: routing 1 + hybrid ~2-5 LLM calls acceptable
```

#### 6B-4.3 — `review/README.md` — "Production configuration" section

One table: dev vs prod env vars.

#### 6B-4.4 — Optional feature flag (defer)

`REVIEW_PRODUCT_MODE=true` → internally sets tenant_auto + hybrid — only if ops wants single switch. **Defer** unless requested; split env is clearer.

### 6.3 Acceptance criteria

- [ ] `.env.production.example` committed
- [ ] README documents prod vs dev
- [ ] CI still uses `COMPLIANCE_MODE=lexical` via `conftest.py`
- [ ] No change to `config.py` default literals

---

## 7. Implementation order

```text
Sprint 1 (ship first — user-visible)
  [ ] 6B-1.1–6B-1.4  policy_title enrich + report
  [ ] Tests

Sprint 2 (accuracy)
  [ ] 6B-2.1–6B-2.3  match prompts + code vars
  [ ] 6B-3.1–6B-3.3  routing hints (Layer A)
  [ ] Tests

Sprint 3 (discovery recall + ops)
  [ ] 6B-3.4           tenant section seed (Layer B)
  [ ] 6B-4.1–6B-4.3   prod env + README + QA checklist
  [ ] Manual E2E on pilot tenant
```

**Dependency:** 6B-2 reuses `build_policy_title_map` from 6B-1 — implement 6B-1 first.

---

## 8. Test plan

| ID | Test | Task |
|----|------|------|
| T1 | `enrich_findings_policy_titles` sets metadata | 6B-1 |
| T2 | E2E report contains `Violated policy:` | 6B-1 |
| T3 | `compare_sections_llm` user prompt contains policy_title | 6B-2 |
| T4 | Batch block contains `Playbook:` | 6B-2 |
| T5 | Routing context includes topic hints | 6B-3 |
| T6 | Tenant section titles in routing when policies indexed | 6B-3 |
| T7 | Discovery finds policy after routing (fixture) | 6B-3 |
| T8 | Config defaults unchanged (request, llm) | 6B-4 |

---

## 9. Out of scope (Phase 7)

- Promote `policy_title` to top-level `ComplianceFinding` field
- UI components for violation display
- Cross-encoder rerank for discovery
- Change grounding to fuzzy match (would weaken hallucination protection)
- `POLICY_CONFLICT` multi-playbook logic

---

## 10. Summary table

| Task | Root bug | Patch | Lines (est.) |
|------|----------|-------|--------------|
| **6B-1** | Title in state, not on finding | `finding_enrich.py` + grounding + report | ~50 |
| **6B-2** | Generic prompts | 2 md files + template vars | ~60 |
| **6B-3** | Topics ≠ search vocabulary | hints yaml + optional section seed | ~50 |
| **6B-4** | Wrong prod config | `.env.production.example` + docs | ~30 |

---

*Document version: 1.0 — Phase 6B output polish & production defaults.*
