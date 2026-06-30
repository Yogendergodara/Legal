# Phase OB-02B / PR-02B — Policy index tag quality (IPC-2)

**Version:** 1.0  
**ID:** `DR-PHASE-OB02B-TAGGER`  
**Parent:** [PHASE_OB01020304_NON429_IPC_RECOVERY_PLAN.md](./PHASE_OB01020304_NON429_IPC_RECOVERY_PLAN.md) · [PHASE_PR01_PRECISION_FUNNEL_RECOVERY_PLAN.md](./PHASE_PR01_PRECISION_FUNNEL_RECOVERY_PLAN.md) · [PHASE_SR01_MEANING_FIRST_RETRIEVAL_PLAN.md](./PHASE_SR01_MEANING_FIRST_RETRIEVAL_PLAN.md)  
**Targets:** `weak_tag_count=0` on Atlassian sync; ↓ `coverage_gate_ipc` / `low_concept_overlap` / `routing_or_skip` on contract smoke  
**Status:** **IMPLEMENTED** (P0 code + tests; operator re-sync required for IPC-2 gate)  
**Scope:** `document_core` ingest-time category tagger only — **no review_agent funnel changes**  
**Effort:** ~0.5 day code + tests; ~0.5 day operator re-sync + validation  
**Risk:** Low if priors stay hint-first; medium if `apply_document_priors` over-appends specifics

**Out of scope:** Review retrieval gates, catalog caps, 429 quota, MCP semaphore (separate plans).

---

## 1. Problem statement

Atlassian smoke sync preflight:

```text
weak_tag_count=1 policies=['Atlassian Acceptable Use Policy']
atlassian-acceptable-use-policy: weak_tags: only broad categories (general/compliance/security)
```

**Effect downstream (review agent, not tagger):**

| Symptom | Mechanism |
|---------|-----------|
| `coverage_gate_ipc` | Section/hit category overlap rejects semantically good hits |
| `low_concept_overlap` | Generic compliance chunks fail evidence lexical gate |
| `routing_or_skip` | Catalog/retrieval cannot narrow to the right policy passage |
| High `obligation_ipc_rate` | Compare never runs — looks like “no policy” |

SR-01 (meaning-first retrieval) is **shipped**; precision layer is working but **index labels are wrong** for AUP.

---

## 2. Root cause (code-proven)

| Layer | Finding |
|-------|---------|
| **Prompt** | `policy_section_categories.md` maps “acceptable use” → `compliance`, `security` only — both are **broad** (`BROAD_POLICY_CATEGORIES`) |
| **Document prior** | AUP `prefer=("compliance", "security")` — reinforces broad output |
| **Prior consumption** | `document_prior_hint()` = soft LLM hint; `apply_document_priors()` = **hard post-process** that **appends** every missing `prefer` tag per section |
| **Sanitize gap** | `_sanitize_llm_categories()` keyword-fills only when LLM returns empty/invalid — **broad-only LLM output passes through** |
| **Keyword fallback** | `metadata_at_ingest.py` has no AUP-specific phrases (prohibited use, DMCA, malware, etc.) |
| **Env** | `document_core/.env` already has `CATEGORY_TAGGER_MODE=llm` — **not an env-only fix** |

**Conclusion:** Re-sync alone may **not** clear `weak_tag_count` until prompt + sanitize + keywords are fixed. Prior changes must avoid **stamping every AUP section** with a long `prefer` list.

---

## 3. Design principles (production)

1. **Specific over broad** — `compliance` / `security` / `general` are fallbacks, not defaults.
2. **Multi-tag per section** — mixed “Prohibited Activities” clauses get **all** applicable specific tags (up to `CATEGORY_TAGGER_MAX_TAGS_PER_SECTION`).
3. **No silent broad-only** — broad-only LLM output triggers keyword re-infer + **warning log** (and sync metadata if needed).
4. **Priors: hint-first, append-conservative** — do not append 4+ specific tags onto every section via `prefer`.
5. **Fix index, not review** — do not lower `evidence_min_concept_overlap` or disable coverage gates to mask bad tags.
6. **Validate distribution, not just count** — `weak_tag_count=0` necessary but not sufficient.

---

## 4. Tagging decision rules (prompt contract)

Add to `document_core/prompts/policy_section_categories.md` under **Label rules**:

```text
### Multi-topic sections (required)
- Read the full section body. Assign every specific tag that genuinely applies (1–5 tags).
- Do not stop at the first pattern match.
- Use compliance / security / general only when no specific tag from the taxonomy fits.
- Never assign only broad tags when the body mentions liability, IP, incidents, access, AI, etc.

### Acceptable Use Policy sections
- Prohibited content, account abuse, resource misuse → access_control (+ incident_reporting if harm/reporting)
- Malware, hacking, unauthorized access → security, access_control, incident_reporting
- Copyright / DMCA / trademark misuse → ip, trademark
- AI / model misuse → ai_usage
- Generic intro / scope / definitions with no obligation → compliance only (acceptable)
```

Replace the single AUP signal row (currently `compliance`, `security` only) with a **subsection** listing sub-signals → specific tags (not broad co-equal options).

---

## 5. Implementation phases

### P0-A — Prompt decision rules (required)

| File | Change |
|------|--------|
| `document_core/prompts/policy_section_categories.md` | Multi-topic rules + AUP subsection; demote broad tags to explicit fallback language |

**Acceptance:** Unit test or snapshot of prompt contains “Do not stop at the first pattern match” and AUP maps to ≥1 specific tag examples.

---

### P0-B — Keyword infer for AUP bodies (required)

| File | Change |
|------|--------|
| `document_core/services/metadata_at_ingest.py` | Add `_CATEGORY_REGEX` / `_CATEGORY_PHRASES` for: `acceptable use`, `prohibited use`, `misuse`, `dmca`, `malware`, `unauthorized access`, `harassment`, `spam`, `cryptomining`, etc. → `access_control`, `incident_reporting`, `ip`, `trademark`, `ai_usage` as appropriate |

**Acceptance:** `tests/test_category_tagger.py` or new `test_metadata_at_ingest_aup.py` — sample AUP clause strings return ≥1 specific tag.

---

### P0-C — Broad-only sanitize safety net (required)

| File | Change |
|------|--------|
| `document_core/services/category_tagger.py` | In `_sanitize_llm_categories()`: if LLM valid tags exist but `specific` is empty, re-run `infer_section_categories_keyword(title, text)`; if keyword yields specific, use keyword result; log `category_tagger: broad_only_fallback section_id=…` |
| Optional | Set ingest extra `tagger_broad_fallback_count` per policy for sync observability |

**Behavior:**

```text
LLM → [compliance, security] only
  → keyword infer on section body
  → if keyword has specific → use specific (+ optional broad)
  → else keep broad (section may truly be generic — OK for intro sections)
```

**Acceptance:** Test — LLM mock returns `["compliance","security"]`, body contains “DMCA” → output includes `ip` or `trademark`.

---

### P0-D — AUP document prior (conservative)

| File | Change |
|------|--------|
| `document_core/services/document_tag_priors.py` | AUP prior: `prefer=("access_control",)` **only** (single anchor, not 4 tags); keep `suppress={sla, payment}`; update `document_prior_hint()` text to list specific candidates for LLM, not auto-append list |

**Do not:** Put `access_control`, `incident_reporting`, `ip`, `ai_usage` all in `prefer` — `apply_document_priors()` would append missing tags to **every** section.

**Optional P1:** Add `append_prefer: bool = True` on `DocumentTagPrior`; set `False` for AUP so hint-only append is disabled for that family.

**Acceptance:** `test_document_tag_priors.py` — AUP section with LLM `["compliance"]` does not gain 4 injected specifics after `_finalize_categories`.

---

### P1-A — Taxonomy `acceptable_use` (optional)

| File | Change |
|------|--------|
| `document_core/schemas/taxonomy.py` | Add `acceptable_use` to `STANDARD_POLICY_CATEGORIES` (specific, not broad) |
| Prompt + tests | Map doc-level AUP title sections to `acceptable_use` where appropriate |

**When:** Only if P0 still leaves document-union weak after spot-check.

---

### P1-B — Sync observability

| File | Change |
|------|--------|
| `document_core/services/policy_sync.py` | Include per-policy `tag_distribution` in sync metadata: top 10 tags, `broad_fallback_count`, `tagger` |
| `temp_java_sync/atlassian_ipc2.py` | Optional warn if any policy has >80% sections sharing identical tag set (over-stamp detector) |

---

## 6. Files touched (summary)

| Path | Phase |
|------|-------|
| `document_core/prompts/policy_section_categories.md` | P0-A |
| `document_core/services/metadata_at_ingest.py` | P0-B |
| `document_core/services/category_tagger.py` | P0-C |
| `document_core/services/document_tag_priors.py` | P0-D |
| `document_core/tests/test_category_tagger.py` | P0 |
| `document_core/tests/test_document_tag_priors.py` | P0-D |
| `document_core/tests/test_metadata_at_ingest_aup.py` (new) | P0-B |
| `document_core/schemas/taxonomy.py` | P1-A (optional) |

**Not changed:** `review_agent/*`, `evidence_sufficiency.py`, `catalog_matcher.py`.

---

## 7. Operator procedure (after P0 ships)

### 7.1 Prerequisites

- document-mcp on **8003**, Postgres **5435**
- `document_core/.env`:

```env
CATEGORY_TAGGER_ENABLED=true
CATEGORY_TAGGER_MODE=llm
CATEGORY_TAGGER_WHOLE_POLICY_ENABLED=true
LLM_API_KEY=<valid Mistral key>
```

- Restart document-mcp after code deploy (reload tagger + prompt)

### 7.2 Re-sync (replace index)

```powershell
cd d:\Ankit_legal\Legal\temp_java_sync
python run_pr01_atlassian_smoke.py   # sync_policies_only replace_policies=True
```

Or battery path with `replace_policies=True` for tenant `atlassian-demo`.

**Do not** use `sync_atlassian_policies.py` with `replace=False` for IPC-2 validation — stale weak tags may remain.

### 7.3 IPC-2 gate

```powershell
python -c "
from atlassian_ipc2 import validate_policy_sync
import json
s = json.load(open('outputs/atlassian_pr01_smoke.json'))  # or sync output
print(validate_policy_sync(s))
"
```

**Pass:** `[]` (empty errors), `weak_tag_count=0`, all policies `tagger=llm`.

---

## 8. Validation matrix (production)

### 8.1 Hard gates

| Check | Pass |
|-------|------|
| `validate_policy_sync()` | No errors |
| `weak_tag_count` | **0** |
| All 9 policies `tagger` | `llm` (not `keyword`) |
| `breaker_open_events_mcp` | 0 on smoke (MCP plan) |

### 8.2 Distribution spot-check (manual, required)

Sample **≥5 AUP sections** from sync output / DB `document_chunks.metadata.categories`:

| Section type | Expected tags (examples) |
|--------------|--------------------------|
| Intro / scope | `compliance` or `acceptable_use` (if P1) — OK alone |
| Prohibited technical abuse | `access_control`, `incident_reporting` and/or `security` |
| Copyright / DMCA | `ip`, `trademark` |
| AI misuse | `ai_usage` |
| Payment / SLA mention | `payment` / `sla` only if text present — not on every section |

**Fail pattern:** Same 4 specific tags on **every** section → prior over-append or prompt over-tag — fix before trusting metrics.

### 8.3 Contract smoke leading indicators

Run `run_pr01_atlassian_smoke.py` (machine awake, MCP healthy, 3 real LLM keys).

| Metric | Baseline (2026-06) | OB-02B target |
|--------|-------------------|---------------|
| `weak_tag_count` | 1 | **0** |
| `low_concept_overlap` (ipc skip) | 8 | **≤ 5** |
| `routing_or_skip` | 11–46 | **≤ 15** |
| `obligation_ipc_rate` | 0.87–0.92 | **↓** (not sole gate; 429 still dominates) |
| `compare_queued` | 24–36 | **≥ 20** stable |
| NC violations | 0–1 | **no regression** on sections 15, 19, 20.4 |

### 8.4 Unit tests (CI)

```powershell
cd d:\Ankit_legal\Legal\document_core
python -m pytest tests/test_category_tagger.py tests/test_document_tag_priors.py tests/test_metadata_at_ingest_aup.py -q
```

---

## 9. Rollback

| Level | Action |
|-------|--------|
| Code | Revert P0 commit; restart document-mcp |
| Index | Re-sync with previous tagger build OR restore DB snapshot |
| Env | `CATEGORY_TAGGER_MODE=keyword` (emergency only — expect `weak_tag_count>0`) |
| Review | No review_agent env rollback needed |

---

## 10. Production checklist

- [x] P0-A prompt merged
- [x] P0-B keyword patterns + tests
- [x] P0-C broad-only sanitize + log
- [x] P0-D AUP prior conservative (no 4-tag append)
- [ ] document-mcp restarted
- [ ] Atlassian re-sync `replace_policies=True`
- [ ] `validate_policy_sync` → `[]`
- [ ] AUP section spot-check (5 sections)
- [ ] Atlassian contract smoke + artifact metrics
- [ ] Plan status → **IMPLEMENTED**

---

## 11. Sequencing with other work

```text
1. MCP global semaphore     → IMPLEMENTED; smoke confirms breaker
2. OB-02B tagger P0 code    → THIS PLAN
3. Re-sync + IPC-2 validate → operator
4. Contract smoke           → IPC / accuracy metrics
5. 429 keys / quota         → parallel (accuracy, not tags)
```

**Do not** lower review evidence gates to simulate OB-02B success.

---

## 12. Success definition

OB-02B is **done** when:

1. Atlassian sync passes IPC-2 with **`weak_tag_count=0`**
2. AUP spot-check shows **sensible per-section** tags (not uniform stamp)
3. Contract smoke shows **↓ `low_concept_overlap`** without NC regression
4. `category_tagger: broad_only_fallback` logs are **rare** post-fix (sanity check that LLM + prompt work, fallback is safety net not primary path)
