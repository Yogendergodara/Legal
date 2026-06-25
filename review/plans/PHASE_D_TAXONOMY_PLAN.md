# Phase D — Taxonomy & Tagging (Implementation Plan)

**Scope:** D1–D7 only. Fix **ingest-time policy tags** — the upstream signal that drives discovery, category filters, and retrieval.  
**Prerequisite:** Phase A LLM tagger enabled (A1/A6–A7). Phase B+C deployed before measuring D7 impact.  
**Principle:** Minimal diffs in `document_core` taxonomy + tagger path; single source of truth in `taxonomy.py`.

---

## Problem summary (Xecurify evidence)

**Source:** `temp_java_sync/outputs/sync_result.json` (all 7 policies `tagger: keyword`).

| Policy | Bad document-level tags | Symptom |
|--------|-------------------------|---------|
| Logo/Trademark Guidelines | `security`, `general` | Trademark policy retrieved for security clauses |
| Terms of Service | `employment`, `sla`, `general` | ToS pollutes HR/SLA discovery |
| Data Retention | `employment`, `ip`, `security` | Deletion sections match wrong policies |
| Code of Conduct | `sla`, `security`, `payment`, `employment` | CoC pulled into unrelated contract topics |
| Incident Response | `sla`, `hr`, `general` | IR plan matches SLA/HR instead of `incident_reporting` |
| Security Practices | `compliance`, `general`, `ip` | Over-broad; weak section precision |
| Privacy Policy | `employment`, `payment` | Body-text magnets, not policy intent |

**Downstream effect:** Contract section classified as `confidentiality` + `data_retention` retrieves policies whose **parent chunks** are tagged `security` or `compliance` → wrong evidence → compare false positives (partially mitigated by Phase B/C, but root fix is tags).

```text
policy ingest → category_tagger (LLM/keyword)
    → SectionNode.categories → parent chunk metadata.categories
    → policy_documents.metadata.categories (union)
    → discovery list_document_ids_by_categories
    → section retrieval category filter
```

---

## Current implementation status

| Component | File | Gap |
|-----------|------|-----|
| Taxonomy (24 labels) | `document_core/schemas/taxonomy.py` | Too coarse; `compliance`/`security` are magnets |
| Keyword fallback | `metadata_at_ingest.py` | Substring `phrase in haystack`; `"sla"` ⊂ `"slavery"` |
| LLM tagger | `category_tagger.py` + `policy_section_categories.md` | 13-line prompt; no examples; caps at 3 but no broad-tag drop |
| Document priors | — | **None** — sections tagged without title context |
| Tag cap / prune | — | Keyword returns **unlimited** matches; no `general` drop |
| Sync preflight | `sync_service.py` | Only `duplicate_primary_categories`; no weak-tag warning |
| Classifier alignment | `section_category_lexical.py` | Uses regex (better than ingest keyword) but **different rules** than ingest |

---

## Pipeline placement

```text
ingest_document (POLICY)
    → tag_policy_sections
        ① resolve document priors from title (D3)
        ② LLM batch OR keyword per section (D2)
        ③ cap + prune broad tags (D4)
        ④ preflight weak-tag check → warnings (D6)
    → build_parent_child_chunks (categories on parent)
    → save_document (union → doc metadata)
```

**D1** expands allowed labels everywhere prompts read `taxonomy_prompt_labels()`.  
**D5** improves LLM path only; **D2–D4** harden keyword fallback (still required when LLM fails or A1 not ops-complete).

---

## D1 — Expand taxonomy (40–60 specific tags)

### Symptom
Broad magnets (`compliance`, `security`, `general`) match half the corpus; no `trademark`, `secure_deletion`, `incident_reporting`, `forced_labor` as first-class tags.

### Root cause
`STANDARD_POLICY_CATEGORIES` has **24** labels. Ingest keyword maps `"conduct"→compliance`, `"security"→security` on any substring. Classifier and retrieval cannot express Xecurify-specific topics.

### Production-grade fix (minimal, backward-compatible)

**Expand to ~45 labels** — add specific tags; keep existing 24 for backward compat.

| Priority | New tags |
|----------|----------|
| **P0 (Xecurify)** | `secure_deletion`, `legal_hold`, `data_subject_rights`, `incident_reporting`, `breach_notification`, `trademark`, `forced_labor`, `modern_slavery`, `anti_bribery`, `aml` |
| **P1** | `cross_border_transfer`, `vendor_due_diligence`, `access_control`, `encryption`, `audit_rights`, `whistleblower`, `records_management`, `business_continuity`, `export_control`, `sanctions` |
| **P2** | `cookie`, `marketing`, `subprocessor`, `dpa`, `background_check`, `workplace_safety`, `diversity`, `gifts_entertainment` |

**Aliases** in `_CATEGORY_ALIASES` (map synonyms → canonical):

```python
"secure_deletion": "secure_deletion",  # also map old data_retention+deletion phrases
"data_subject_rights": "data_subject_rights",
"gdpr": "data_subject_rights",
"dpdpa": "data_subject_rights",
"incident_response": "incident_reporting",
"logo_usage": "trademark",
"anti_corruption": "anti_bribery",
```

**Do not remove** `compliance`, `security`, `general` — mark as **broad** in `taxonomy.py`:

```python
BROAD_POLICY_CATEGORIES = frozenset({"general", "compliance", "security"})
```

**Sync classifier lexical** (`section_category_lexical.py`): add regex rows for new P0 tags only (~15 lines). Single import from `taxonomy.py` — no duplicate label lists.

### Files
| File | Change |
|------|--------|
| `document_core/schemas/taxonomy.py` | +25 tags, aliases, `BROAD_POLICY_CATEGORIES`, `taxonomy_classifier_table()` ~40 LOC |
| `review_agent/services/section_category_lexical.py` | P0 regex rows ~20 LOC |
| `tests/test_taxonomy.py` | **new** normalize + alias cases |

### Verify
- `GET /api/taxonomy` returns expanded set
- `normalize_categories(["gdpr"])` → `data_subject_rights`
- Existing tests still pass (old tags valid)

**LOC:** ~60 production + tests

---

## D2 — Harden keyword fallback

### Symptom
- `"sla"` tag from **"slavery"** / **"modern slavery"** in CoC body  
- `"security"` from **"brand security"** in Logo Guidelines  
- `"ip"` from title tokenization on unrelated sections

### Root cause
`metadata_at_ingest.py` `_infer_categories()`:

```python
if phrase in haystack:          # substring — "sla" matches "slavery"
for token in re.split(...):     # title tokens → category without boundary check
    normalize_categories([token])
```

No title-vs-body weighting; short tokens match inside longer words.

### Production-grade fix (minimal)

**New helpers in `metadata_at_ingest.py` (~55 LOC):**

```python
_SHORT_TOKEN_PHRASES = frozenset({"sla", "ip", "hr", "ai", "dr", "mss"})

def _phrase_matches(phrase: str, text: str) -> bool:
    if phrase in _SHORT_TOKEN_PHRASES or len(phrase) <= 3:
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text))
    return phrase in text

def _infer_categories_keyword_section(*, title: str, text: str) -> list[str]:
    title_hay = title.lower()
    body_hay = (text or "")[:2000].lower()
    found: list[str] = []
    # 1) title phrases first (higher confidence)
    # 2) body phrases with _phrase_matches only
    # 3) title tokens with word-boundary + allowlist (no bare "sla" from title unless "SLA" context)
```

**Replace dangerous phrase** `("sla", "sla")` with regex-only:

```python
(r"\bsla\b|service level agreement", "sla"),
```

**Remove** bare `("security", "security")` — replace with:

```python
(r"\binformation security\b|\bcybersecurity\b|\bsecurity control", "security"),
```

Keep `infer_section_categories_keyword()` as public API; refactor `_infer_categories` to use new logic.

### Files
| File | Change |
|------|--------|
| `metadata_at_ingest.py` | refactor ~55 LOC |
| `tests/test_category_tagger.py` | +4 cases: slavery≠sla, brand security, logo title |

### Verify
```python
infer_section_categories_keyword(title="Code of Conduct", text="modern slavery provisions")
# → human_rights (NOT sla)

infer_section_categories_keyword(title="Logo Guidelines", text="brand security requirements")
# → ip or trademark (NOT security)
```

**LOC:** ~55

---

## D3 — Document-level tag priors

### Symptom
CoC sections tagged `employment`, `sla`, `payment`; Logo doc tagged `security`; ToS tagged `employment`.

### Root cause
Tagger treats each section **in isolation**. Document title is passed to LLM prompt but **not** used in keyword fallback. No suppress list for known false positives per policy family.

### Production-grade fix (minimal)

**New `document_tag_priors.py` (~50 LOC)** in `document_core/services/`:

```python
@dataclass(frozen=True)
class DocumentTagPrior:
    title_keys: tuple[str, ...]      # substring match on lowered title
    prefer: tuple[str, ...]          # inject if missing (max 2)
    suppress: frozenset[str]         # remove from section tags

_DOCUMENT_PRIORS: tuple[DocumentTagPrior, ...] = (
    DocumentTagPrior(
        ("code of conduct", "conduct"),
        ("human_rights", "compliance"),
        frozenset({"sla", "employment", "payment", "security"}),
    ),
    DocumentTagPrior(
        ("logo", "trademark"),
        ("trademark", "ip"),
        frozenset({"security", "compliance", "general"}),
    ),
    DocumentTagPrior(
        ("terms of service", "terms of use"),
        ("governing_law",),
        frozenset({"employment", "sla", "hr"}),
    ),
    DocumentTagPrior(
        ("data retention",),
        ("data_retention", "secure_deletion"),
        frozenset({"employment", "ip", "security"}),
    ),
    DocumentTagPrior(
        ("incident response",),
        ("incident_reporting", "security"),
        frozenset({"sla", "hr", "general"}),
    ),
    DocumentTagPrior(
        ("privacy",),
        ("privacy", "data_subject_rights"),
        frozenset({"employment", "payment"}),
    ),
)

def apply_document_priors(categories: list[str], *, document_title: str) -> list[str]:
    ...
```

**Wire:**
- `category_tagger.py` — after LLM/keyword assign per node: `node.categories = apply_document_priors(node.categories, document_title=...)`
- `apply_keyword_tags()` — same

**LLM prompt** (D5): pass `document_title` priors hint: *"This document is a Code of Conduct — prefer human_rights, not sla/employment."*

### Files
| File | Change |
|------|--------|
| `services/document_tag_priors.py` | **new** ~50 LOC |
| `category_tagger.py` | wire ~8 LOC |
| `tests/test_document_tag_priors.py` | **new** 5 cases |

### Verify
- CoC section: no `sla` after priors
- Logo doc union: `trademark`/`ip`, not `security`

**LOC:** ~58

---

## D4 — Cap tags per section

### Symptom
Keyword path returns 6–10 categories per section; document union inflates to 8–10 tags per policy.

### Root cause
- Keyword: no cap in `_infer_categories`  
- LLM: `[:3]` slice in `category_tagger.py:75` but **no** drop of `general`/`compliance` when specific exists  
- `retrieval_relevance._BROAD_CATEGORIES` only excludes `general`, `compliance` — not `security`

### Production-grade fix (minimal)

**New `cap_section_categories()` in `taxonomy.py` (~25 LOC):**

```python
def cap_section_categories(
    categories: list[str],
    *,
    max_tags: int = 3,
    broad: frozenset[str] = BROAD_POLICY_CATEGORIES,
) -> list[str]:
    norm = normalize_categories(categories)
    specific = [c for c in norm if c not in broad]
    broad_only = [c for c in norm if c in broad]
    if specific:
        return specific[:max_tags]
    return (broad_only or ["general"])[:max_tags]
```

**Wire:**
- `category_tagger.py` — after every assign (LLM + keyword + priors): `node.categories = cap_section_categories(node.categories)`
- Config: `CATEGORY_TAGGER_MAX_TAGS_PER_SECTION=3` (default 3)

**Align retrieval:** add `security` to `_BROAD_CATEGORIES` in `retrieval_relevance.py` (~1 line) — consistent with ingest prune.

### Files
| File | Change |
|------|--------|
| `taxonomy.py` | `cap_section_categories` ~25 LOC |
| `category_tagger.py` | wire ~5 LOC |
| `config.py` | `category_tagger_max_tags_per_section: int = 3` |
| `retrieval_relevance.py` | add `security` to broad set |
| `tests/test_category_tagger.py` | cap + broad-drop cases |

### Verify
- Section with `[confidentiality, compliance, security, privacy]` → `[confidentiality, privacy]` (+ maybe 1 more specific)
- Logo section: max 3 tags

**LOC:** ~35

---

## D5 — Improve LLM tagger prompt

### Symptom
Even with `tagger: llm`, model picks `compliance` + `security` for most sections (prompt has no guidance).

### Root cause
`policy_section_categories.md` is **13 lines** — label list only, no examples, no anti-patterns, no document-type context.

### Production-grade fix (minimal)

Expand prompt to ~45 lines (not 200):

```markdown
### Rules
1. Assign 1–3 categories. Most specific wins.
2. Do NOT use compliance/security/general if a specific tag fits.
3. Use document title as primary signal for policy family.

### Examples
| Section title | Text snippet | Categories |
| Code of Conduct - Anti-Harassment | workplace respect... | human_rights, compliance |
| Data Retention - Secure Deletion | delete within 30 days... | secure_deletion, data_retention |
| Logo Usage | do not alter trademark... | trademark, ip |

### Anti-patterns (NEVER)
| Wrong | Why | Correct |
| modern slavery paragraph | NOT sla | human_rights, forced_labor |
| brand security guidelines | NOT security (cyber) | trademark, ip |
| payment terms in ToS | only if section is about billing | payment |

Document: {document_title}
Allowed: {taxonomy_labels}
```

**Pass prior hint** from D3 when title matches known family (~1 line injected).

### Files
| File | Change |
|------|--------|
| `prompts/policy_section_categories.md` | rewrite ~45 lines |
| `category_tagger.py` | inject `prior_hint` from `document_tag_priors` ~5 LOC |

### Verify
- Mocked LLM test: CoC batch returns `human_rights` not `sla`
- Manual: one policy re-sync with `tagger: llm` → inspect parent categories in DB

**LOC:** ~10 code + prompt text

---

## D6 — Preflight weak-tag warning at sync

### Symptom
`sync_result.json` shows polluted tags but `"warnings": []` on every policy. Operator has no signal to re-sync.

### Root cause
`sync_service.sync_policies_only` preflight only computes `duplicate_primary_categories`. Ingest warnings don't include tag-quality checks.

### Production-grade fix (minimal)

**New `assess_tag_quality()` in `document_tag_priors.py` or `taxonomy.py` (~30 LOC):**

```python
def assess_policy_tag_quality(
    *,
    document_title: str,
    section_categories: list[list[str]],  # per-parent
    tagger: str,
) -> list[str]:
    warnings = []
    if tagger == "keyword":
        warnings.append("tagger=keyword; re-sync with CATEGORY_TAGGER_MODE=llm recommended")
    union = set(normalize_categories(flatten(section_categories)))
    specific = union - BROAD_POLICY_CATEGORIES
    if not specific or union <= BROAD_POLICY_CATEGORIES:
        warnings.append("weak_tags: only broad categories (general/compliance/security)")
  # per prior: if title matches CoC but union has sla → warn
    return warnings
```

**Wire `sync_service.py`:**
- After ingest, fetch parent sections via `list_sections` or from ingest result metadata
- Append warnings to each policy result + aggregate `preflight.weak_tag_policies`

### Files
| File | Change |
|------|--------|
| `taxonomy.py` or `document_tag_priors.py` | `assess_policy_tag_quality` ~30 LOC |
| `sync_service.py` | fetch parents + warn ~25 LOC |
| `tests/test_sync_tag_warnings.py` | **new** 2 cases |

### Verify
- `sync_result.json` → Logo policy `warnings` contains `weak_tags` or `unexpected:security`
- `preflight.weak_tag_count` > 0 for keyword baseline

**LOC:** ~55

---

## D7 — Re-index + re-run Xecurify

### Purpose
Measure tag-layer impact after D1–D6.

### Procedure

```powershell
# 1. Restart MCP (load document_core + taxonomy changes)
cd "d:\Ankit_legal\Legal\Legal ai\scripts"
.\start_document_mcp.ps1 -Replace

# 2. Confirm document_core/.env
# CATEGORY_TAGGER_MODE=llm
# LLM_API_KEY=...

# 3. Re-sync policies
cd "d:\Ankit_legal\Legal\temp_java_sync"
python test_xecurify_policies.py   # or Dev UI sync

# 4. Re-run review → export assessment
python export_assessment.py
# Save: outputs/xecurify_nda_assessment_post_d.json
```

### Record (before vs after)

| Metric | Baseline (keyword) | Target (post-D) |
|--------|-------------------|-----------------|
| `tagger` | `keyword` all 7 | `llm` all 7 |
| Logo doc categories | `security`, `general` | `trademark`, `ip` |
| CoC union contains `sla` | yes | no |
| Avg tags per section | ~4–8 | ≤3 |
| Policies with weak-tag warning | 7 | 0 |
| Wrong-policy retrievals (manual) | high | ≤2 sections |
| Weighted alignment (post B+C) | TBD | +5–10 vs post-C |

---

## Execution order (minimal PRs)

```text
PR-1  D2 + D4           ~90 LOC   keyword hardening + cap (works immediately)
PR-2  D3 + D6           ~85 LOC   document priors + sync warnings
PR-3  D1 (P0 tags)      ~80 LOC   taxonomy expand + lexical classifier rows
PR-4  D5                ~15 LOC   LLM prompt + prior hint
PR-5  D7                ops       re-index + measure
```

**Why this order:** D2+D4 fix keyword path without taxonomy blast radius. D3 fixes Xecurify policy families. D1 adds labels once pruning works. D5 improves LLM when A1 ops complete.

---

## File change matrix

| File | D1 | D2 | D3 | D4 | D5 | D6 |
|------|----|----|----|----|----|-----|
| `schemas/taxonomy.py` | edit | | | edit | | edit |
| `services/metadata_at_ingest.py` | | edit | | | | |
| `services/document_tag_priors.py` | | | new | | hint | edit |
| `services/category_tagger.py` | | | wire | wire | wire | |
| `prompts/policy_section_categories.md` | | | | | edit | |
| `config.py` | | | | +1 | | |
| `sync_service.py` | | | | | | edit |
| `section_category_lexical.py` | edit | | | | | |
| `retrieval_relevance.py` | | | | align | | |
| tests (4 files) | new | edit | new | edit | edit | new |

**Total new production code:** ~200 LOC (excluding prompt prose and tests).  
**Compatible:** extends `taxonomy.py` single source; no graph topology change; Phase B relevance gate benefits from cleaner tags.

---

## What NOT to do

| Avoid | Why |
|-------|-----|
| 60+ tags in PR-1 | Blast radius; start P0 ~11 tags |
| Separate taxonomy per service | Drift; always `taxonomy.py` |
| ML re-ranker for tags | Over-engineering; rules + LLM sufficient |
| Re-tag contracts at ingest | Out of scope; contract uses `section_classifier` |
| Remove `compliance`/`security` entirely | Breaks existing indexed docs until re-sync |
| Full RAG on tag validation | Cost/latency; deterministic priors enough |

---

## Acceptance criteria (Phase D complete)

- [ ] D1: P0 tags in `STANDARD_POLICY_CATEGORIES`; aliases resolve
- [ ] D2: `sla` not inferred from `slavery`; `security` not from `brand security`
- [ ] D3: CoC priors suppress `sla`/`employment`; Logo suppresses `security`
- [ ] D4: ≤3 tags per section; `general`/`compliance`/`security` dropped when specific exists
- [ ] D5: LLM prompt has examples + anti-patterns; prior hint in user block
- [ ] D6: `sync_result.json` warnings for keyword tagger and weak-only unions
- [ ] D7: `xecurify_nda_assessment_post_d.json` saved; tag metrics recorded
- [ ] All `test_category_tagger.py` + taxonomy tests green
- [ ] `test_acme_nda_e2e` regression pass

---

## Dependency on Phase A

Phase D **amplifies** LLM tagging (D5) but D2–D4 improve **keyword fallback** regardless. Until A6–A7 complete (`tagger: llm`), prioritize **PR-1 + PR-2** — they fix the current `sync_result.json` pollution without API key.

After A7: run **PR-4 + D7** to validate full LLM + taxonomy stack.
