# Phase E — Grounding & Compare Precision (Implementation Plan)

**Scope:** E1–E4 only. Reduce **INCONCLUSIVE** and **INSUFFICIENT_POLICY_CONTEXT** without weakening real violations.  
**Prerequisite:** Phase B+C (retrieval/compare) and Phase D (tagging) deployed; re-run Xecurify baseline after D7.  
**Principle:** Deterministic quote normalization + targeted grounding exceptions — no removal of grounding for NON_COMPLIANT.

---

## Problem summary (Xecurify evidence)

**Source:** `temp_java_sync/outputs/xecurify_nda_assessment.json` (pre B/C/D fixes).

| Metric | Baseline | Target (E) |
|--------|----------|------------|
| INCONCLUSIVE sections | **8 / 30 (27%)** | **< 10%** (≤3) |
| INSUFFICIENT_POLICY_CONTEXT | **7 / 30 (23%)** | **< 5%** (≤1) |
| Sections not confidently reviewed | **~50%** (15/30) | **< 20%** |
| Grounding downgrades | 4–5 | ≤2 |
| Quote repair success | 2 / 6 | ≥4 / 6 |
| Weighted alignment | 47.7 | ≥75 (with B–D) |

### Failure modes (from assessment)

| Pattern | Example | Layer |
|---------|---------|-------|
| Compare-time quote downgrade | *"aligns with policy" (Downgraded: model quotes were not exact substrings)* | `quote_validate.py` |
| Grounding fails on bullet lists | CoC §5.2 labor clauses — `•` in contract quote | `grounding.py` + MCP `verify_quote` |
| COMPLIANT + empty `policy_quote` | Exclusion clauses vs Security policy — alignment clear, no policy span needed | `quote_validate` + `grounding_node` |
| IPC from wrong playbook | Governing Law → Incident Response Plan | Retrieval (B/D); compare still emits IPC |
| Too many hits in compare batch | 3 category-aligned hits, weak overlap → noisy compare | `compare_hit_selection.py` |

```text
section_compare_llm
    → validate_and_normalize_quotes     ← E2 (bullets) + E1 (empty policy OK)
    → merge → final_gap_verify
    → grounding_node (verify_quote)     ← E2 (shared normalize) + E1 (COMPLIANT relax)
    → guard_pass → report
```

---

## Current implementation status

| ID | Status | What exists |
|----|--------|-------------|
| **E1** | ❌ | Grounding requires both quotes when present; no COMPLIANT + empty-policy exception |
| **E2** | 🟡 Partial | `quote_validate`: whitespace normalize only; `grounding.py`: `\s+` collapse — **no bullet/list tolerance** |
| **E3** | ❌ | No confidence metrics breakdown by downgrade source |
| **E4** | 🟡 Default | `category_aligned` + `compare_max_policy_hits=3` — no relevance floor on selected hits |

---

## E1 — Relax grounding for aligned COMPLIANT (empty policy quote)

### Symptom
Findings marked INCONCLUSIVE with rationale *"aligns with the policy"* and `policy_quote: ""`, `grounded: yes` — should remain **COMPLIANT** (info).

Examples from baseline:
- Exclusion of Publicly Available Information
- Support for Human Rights / environmental CoC clauses
- Incorporation-by-reference clauses (policy adopted by name, no policy span to quote)

### Root cause (dual)

1. **`validate_and_normalize_quotes()`** (`quote_validate.py:106–118`): For `COMPLIANT`/`NON_COMPLIANT`, requires **both** `contract_ok` and `policy_ok`. When policy text is in compare batch but model correctly leaves `policy_quote` empty (incorporation / exclusion clause), `policy_ok=False` → forced INCONCLUSIVE.

2. **`grounding_node`** (`nodes.py:234`): `ok = contract_ok and policy_ok`. Empty `policy_quote` skips verify (policy_ok stays True), but compare-time downgrade already happened. Separate case: COMPLIANT with contract quote failing E2 bullet match → grounding downgrade.

### Production-grade fix (minimal)

**Rule:** Allow empty `policy_quote` for **COMPLIANT** only when:
- `contract_ok` is True (after E2 normalize), AND
- Rationale indicates alignment (`aligns with`, `incorporation`, `adoption`, `no deviation`, `satisfies`), AND
- Status is COMPLIANT (never relax NON_COMPLIANT — violations need policy evidence)

**New helper** `quote_validate.py` (~25 LOC):

```python
_ALIGNMENT_RATIONALE = re.compile(
    r"(?i)\b(aligns? with|no deviation|satisfies|incorporation|adopted by reference|"
    r"consistent with|complies with)\b"
)

def allows_empty_policy_quote(status, rationale, *, contract_ok: bool) -> bool:
    return (
        status == ComplianceStatus.COMPLIANT
        and contract_ok
        and _ALIGNMENT_RATIONALE.search(rationale or "")
    )
```

**Wire in `validate_and_normalize_quotes()`:**

```python
if result.status in (COMPLIANT, NON_COMPLIANT):
    if allows_empty_policy_quote(result.status, result.rationale, contract_ok=contract_ok):
        policy_ok = True  # COMPLIANT with clear alignment — policy span optional
    if not contract_ok or not policy_ok:
        ... downgrade ...
```

**Wire in `grounding_node`** (~12 LOC): if finding is COMPLIANT, contract_ok, empty policy_quote, alignment rationale → append grounded without downgrade (belt-and-suspenders).

**Config:** `GROUNDING_RELAX_COMPLIANT_EMPTY_POLICY=true` (default on)

### Files
| File | Change |
|------|--------|
| `services/quote_validate.py` | helper + wire ~30 LOC |
| `graph/nodes.py` | COMPLIANT relax branch ~12 LOC |
| `config.py` | 1 flag |
| `tests/test_quote_validate.py` | 2 cases |
| `tests/test_grounding_downgrade.py` | 1 case |

### Verify
- COMPLIANT + empty policy_quote + *"aligns with policy"* + valid contract quote → stays COMPLIANT through grounding
- NON_COMPLIANT + empty policy_quote → still INCONCLUSIVE/downgraded

**LOC:** ~45

---

## E2 — Bullet / format-tolerant quote matching

### Symptom
Valid findings downgraded with *"Downgraded: model quotes were not exact substrings"* when:
- Contract uses `•` bullet lists; model quote includes `•` but source has `-` or no marker
- Extra whitespace / line breaks differ
- CoC labor clauses: `grounded: no` despite obvious substring match

Baseline: **≥8 INCONCLUSIVE** carry downgrade suffix; **4 grounding downgrades** on CoC §5.2.

### Root cause

| Location | Logic | Gap |
|----------|-------|-----|
| `grounding.py` `normalize_text()` | `\s+` → single space | No bullet stripping |
| `quote_validate.py` `quote_is_substring()` | `q in haystack` or whitespace collapse | No list-marker normalization |
| Compare prompt | Demands exact substring | Model copies `•` from rendered batch |

### Production-grade fix (minimal)

**Single shared normalizer** — avoid drift between compare validate and MCP grounding.

**New `quote_normalize.py`** (~40 LOC) in `review_agent/services/` (or extend `document_core/services/grounding.py` and import in review):

```python
_BULLET_RE = re.compile(r"^[\s•●▪◦\-\*]+", re.MULTILINE)
_LIST_MARKER_RE = re.compile(r"[\s]*[•●▪◦]\s*")

def normalize_for_quote_match(text: str) -> str:
  t = (text or "").replace("\u2022", "•")
  t = _LIST_MARKER_RE.sub(" ", t)
  t = re.sub(r"\s+", " ", t.strip().lower())
  return t

def quote_matches(quote: str, haystack: str) -> bool:
    qn = normalize_for_quote_match(quote)
    hn = normalize_for_quote_match(haystack)
    if not qn:
        return False
    return qn in hn
```

**Wire:**
- `quote_validate.py` — `quote_is_substring` delegates to `quote_matches`
- `document_core/services/grounding.py` — `_match_in_text` uses same function (move to `document_core/services/quote_match.py` to share without circular imports)

**Prefer:** `document_core/services/quote_match.py` imported by both `grounding.py` and `quote_validate.py` (review already depends on document_core).

**Optional:** Run normalize before `repair_quote_for_section` to improve repair hit rate.

### Files
| File | Change |
|------|--------|
| `document_core/services/quote_match.py` | **new** ~40 LOC |
| `document_core/services/grounding.py` | use `quote_matches` ~5 LOC |
| `review_agent/services/quote_validate.py` | use `quote_matches` ~5 LOC |
| `document_core/tests/test_grounding.py` | bullet cases |
| `tests/test_quote_validate.py` | bullet list case |

### Verify
```python
quote_matches(
    "• Support and respect internationally proclaimed human rights",
    "Support and respect internationally proclaimed human rights",
)  # True

quote_matches("• Hold all Confidential Information", "Hold all Confidential Information in strict confidence")  # True
```

**LOC:** ~50

---

## E3 — Target: <10% INCONCLUSIVE, <5% IPC

### Symptom
50% of sections lack confident outcome (INCONCLUSIVE + IPC). Stakeholders see "review incomplete" on half the NDA.

### Root cause (attribution)

| Source | Baseline contrib. | Fix phase |
|--------|-------------------|-----------|
| Quote substring downgrade (E2) | ~8 findings | E2 |
| Empty policy quote on COMPLIANT (E1) | ~6 findings | E1 |
| Wrong policy retrieved → IPC | 7 sections | B, D (already coded) |
| LLM true ambiguity | ~2–3 | Prompt tweak (optional) |
| Coverage backfill / unclear skip | 24 unclear skipped | Tune final_gap_verify thresholds |

E3 is primarily **measurement + acceptance** after E1–E2–E4 and B–D ops re-run — not a separate large feature.

### Production-grade fix (minimal)

**Add confidence metrics** to `compliance_stats` / artifact (~35 LOC):

```python
def compute_review_confidence_metrics(findings, sections_total) -> dict:
    by_status = count_by_status_per_section(...)
    return {
        "inconclusive_section_pct": ...,
        "ipc_section_pct": ...,
        "confident_section_pct": ...,  # COMPLIANT + NON_COMPLIANT grounded
        "downgrade_quote_validate": ...,  # rationale contains "Downgraded: model quotes"
        "downgrade_grounding": ...,       # metadata grounding_failed
    }
```

**Wire:** `report_node` or `review_artifact.py` — expose in assessment export (`export_assessment.py`).

**Optional prompt** (1 line in `section_compare.md`): For COMPLIANT exclusion/incorporation clauses, `policy_quote` may be `""` if contract alone demonstrates alignment.

### Targets (post E1+E2+E4 + B+C+D re-index)

| Metric | Baseline | Target |
|--------|----------|--------|
| INCONCLUSIVE sections | 8 (27%) | **≤3 (10%)** |
| IPC sections | 7 (23%) | **≤1 (5%)** |
| Confident sections | 15 (50%) | **≥24 (80%)** |
| `downgrade_quote_validate` count | ~8 | **≤2** |
| `downgrade_grounding` count | 4 | **≤2** |

### Files
| File | Change |
|------|--------|
| `services/review_confidence.py` | **new** ~35 LOC |
| `services/review_artifact.py` | wire metrics |
| `temp_java_sync/export_assessment.py` | include confidence block |

**LOC:** ~45

---

## E4 — Tune compare hit selection (post B/C)

### Symptom
Compare batches include **weak category-aligned hits** → model compares against wrong policy chunks → IPC or false NC. Example: Security Measures compared to Data Retention deletion chunk (fixed partly by B3 coverage gate; still noisy with 3 hits).

### Root cause

`select_compare_hits()` (`compare_hit_selection.py`):
- `category_aligned` returns up to **`compare_max_policy_hits=3`** with any category intersection
- No **relevance score floor** on selected hits (relevance gate runs earlier but aligned set can still be weak)
- Broad tags (`compliance`, `security`) cause false alignment (Phase D reduces this)

### Production-grade fix (minimal)

**Two knobs** — config only after measuring post-D sync:

| Setting | Baseline | Recommended (NDA) | Rationale |
|---------|----------|-------------------|-----------|
| `COMPARE_MAX_POLICY_HITS` | 3 | **2** | Fewer policies in prompt → less confusion |
| `COMPARE_POLICY_HIT_MODE` | `category_aligned` | **`category_aligned`** (keep) or `primary_only` for boilerplate | primary_only too aggressive for multi-policy sections |

**Code enhancement** (~30 LOC) — relevance-aware selection:

```python
def select_compare_hits(hits, *, section_categories, section_title, settings):
    ...
    if mode == "category_aligned":
        aligned = [h for h in hits if section_cats & hit_cats(h)]
        # NEW: score and filter
        scored = [
            (h, score_hit_relevance(h, section_categories=..., section_title=...))
            for h in aligned
        ]
        scored = [(h, s) for h, s in scored if s >= cfg.compare_hit_min_relevance_score]
        if scored:
            scored.sort(key=lambda x: x[1], reverse=True)
            return [h for h, _ in scored[:cap]]
        return hits[:1]  # existing fallback
```

**Config:** `COMPARE_HIT_MIN_RELEVANCE_SCORE=0.35` (default; reuse `retrieval_relevance_min_score` or separate)

### Files
| File | Change |
|------|--------|
| `services/compare_hit_selection.py` | relevance filter ~30 LOC |
| `config.py` + `.env.example` | `compare_hit_min_relevance_score` |
| `tests/test_compare_hit_selection.py` | 1 case: weak hit dropped |

### Verify
- Section `security` + hits [security@0.5, data_retention@0.1] → only security hit in compare batch
- `avg_hits_per_section` in compare stats drops from ~2.5 → ~1.5

**LOC:** ~35

---

## Execution order (minimal PRs)

```text
PR-1  E2              ~50 LOC   shared quote_match — fixes most INCONCLUSIVE downgrades
PR-2  E1              ~45 LOC   COMPLIANT empty-policy relax
PR-3  E4              ~35 LOC   relevance-aware hit cap (after D7 re-sync)
PR-4  E3              ~45 LOC   confidence metrics + export
PR-5  ops             re-run Xecurify → xecurify_nda_assessment_post_e.json
```

**Do E2 before E1** — many E1 candidates fail contract_ok until bullets normalize.

---

## File change matrix

| File | E1 | E2 | E3 | E4 |
|------|----|----|----|-----|
| `document_core/services/quote_match.py` | | new | | |
| `document_core/services/grounding.py` | | edit | | |
| `review_agent/services/quote_validate.py` | edit | edit | | |
| `review_agent/graph/nodes.py` | edit | | | |
| `review_agent/services/compare_hit_selection.py` | | | | edit |
| `review_agent/services/review_confidence.py` | | | new | |
| `review_agent/config.py` | +1 | | | +1 |
| `prompts/section_compare.md` | hint | | | |
| `export_assessment.py` | | | edit | |
| tests (5 files) | new | new | new | edit |

**Total new production code:** ~175 LOC.  
**No graph topology change.** NON_COMPLIANT grounding unchanged.

---

## What NOT to do

| Avoid | Why |
|-------|-----|
| Disable grounding for NON_COMPLIANT | Audit trail breaks; legal risk |
| Fuzzy match >95% similarity | False positives; substring + normalize enough |
| LLM second pass for grounding | Cost/latency; E2 fixes root cause |
| `primary_only` globally without measurement | May miss multi-policy sections |
| Lower IPC by suppressing IPC findings | Hides retrieval gaps; fix B/D instead |

---

## Acceptance criteria (Phase E complete)

- [ ] E2: bullet-list contract quotes ground successfully
- [ ] E1: COMPLIANT + empty policy_quote + alignment rationale stays COMPLIANT
- [ ] E4: compare batch excludes relevance score < threshold hits
- [ ] E3: `inconclusive_section_pct` ≤ 10%, `ipc_section_pct` ≤ 5% on post-D Xecurify run
- [ ] `xecurify_nda_assessment_post_e.json` saved with confidence metrics
- [ ] NON_COMPLIANT violations still grounded (regression: `test_acme_nda_e2e`)
- [ ] All pytest green

---

## Dependency chain

```text
Phase A (LLM tagger) → Phase D (clean tags)
Phase B (retrieval gate) → fewer wrong IPC
Phase C (compare guards) → fewer false NC
Phase E (grounding precision) → fewer INCONCLUSIVE on valid findings
```

**Measure E against post-D baseline**, not pre-B baseline — otherwise IPC improvements from B/D mask E-only gains.

### D7 + E5 ops command

```powershell
cd "d:\Ankit_legal\Legal\temp_java_sync"
python test_xecurify_policies.py
python export_assessment.py
# Compare: xecurify_nda_assessment.json (pre)
#          xecurify_nda_assessment_post_d.json (post-D)
#          xecurify_nda_assessment_post_e.json (post-E)
```
