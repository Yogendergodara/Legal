# Phase C — Compare Intelligence (Implementation Plan)

**Scope:** C1–C6 only. Minimal diffs. Deterministic guards before/after LLM compare — not more model calls.  
**Prerequisite:** Phase B retrieval safety deployed (B1–B3). Phase A baseline run (A6–A8) for before/after metrics.

---

## Problem summary (Xecurify evidence)

| Symptom | Example | Layer |
|---------|---------|-------|
| False NON_COMPLIANT despite adoption by name | Code of Conduct: contract says *"agrees to uphold Xecurify's Code of Conduct principles"* | Compare input + LLM judgment |
| Contradictory findings same topic | §2.1 NON_COMPLIANT secure deletion; §3.2 COMPLIANT secure deletion | Per-section compare isolation |
| Requirement inflation | Data Principal Rights CRITICAL though contract has *"subject to applicable legal retention requirements"* | Compare LLM + no equivalence guard |
| Violation from wrong evidence | Security section compared to Data Retention deletion chunk | Retrieval (Phase B) + compare gate |

Phase C fixes **compare-layer judgment** after retrieval is cleaned up.

---

## Pipeline placement

```text
section_policy_retrieval
    → section_compare_llm_node
        ① apply_coverage_gate (C5)           ← before compare
        ② build related section context (C2) ← in compare prompt
        ③ compare LLM + prompt rules (C1)
        ④ apply_incorporation_guard (C1 code)  ← after compare, before merge
    → merge_section_findings
        ⑤ suppress_contradicted (C3)
        ⑥ apply_equivalence_guard (C4)       ← after merge or in prepare_compare_items
    → grounding → report
```

---

## Current implementation status

| ID | Status | What exists today |
|----|--------|-------------------|
| **C1** | 🟡 Partial | Prompt rule in `section_compare.md` lines 44–46 only |
| **C2** | 🟡 Partial | `section_cross_reference.py` — survival ranges + explicit `section N` refs only; **no** category sibling bundling |
| **C3** | 🟡 Partial | `suppress_contradicted_non_compliant()` — exact `dimension_label` match only |
| **C4** | ❌ Not started | — |
| **C5** | ✅ Done | `apply_coverage_gate()` in `section_compare_nodes.py` |
| **C6** | ⏳ Ops | Re-run `test_xecurify_policies.py` / Dev UI review |

---

## C1 — Incorporation-by-reference

### Symptom
`Explicit Acknowledgment of Code of Conduct — NON_COMPLIANT` while contract says:
> *the Receiving Party agrees to uphold Xecurify's Code of Conduct principles*

### Root cause (dual)

1. **Prompt-only is insufficient.** LLM compare prompt says incorporation → COMPLIANT, but also says *"contract_quote must come from the same section_id"* and *"Do not skip any policy requirement"*. Model invents a gap ("does not explicitly acknowledge full scope") despite adoption language.

2. **No deterministic guard.** Nothing in code detects *adopt policy by name* and overrides a false NON_COMPLIANT.

### Production-grade fix (minimal)

**Two layers** (keep both — defense in depth):

| Layer | Mechanism | LOC |
|-------|-----------|-----|
| **C1a Prompt** | ✅ Already in `section_compare.md` — tighten one line: *"Adoption by name satisfies acknowledgment; do not require verbatim policy text in the contract."* | ~3 lines |
| **C1b Code guard** | New `incorporation_guard.py` — post-compare, pre-merge | ~55 LOC |

**C1b algorithm** (`apply_incorporation_guard(items, sections_by_id)`):

```text
FOR each NON_COMPLIANT item:
  section_text = sections[item.section_id].text
  policy_keys = extract_named_policy_title_keys(section_text)  # reuse B1 helper
  IF policy_keys empty → skip
  IF rationale contains "acknowledge|adopt|reference|uphold|comply with" negation
     AND contract_quote matches adoption pattern (policy name within ±80 chars of agree/comply/uphold)
     AND NOT rationale cites concrete contradiction (numeric threshold, prohibited term)
  THEN → status COMPLIANT, severity info, rationale suffix "(Incorporation by reference detected.)"
```

**Wire:** `section_compare_nodes.py` after `compare_all_sections`, before return; or first line of `prepare_compare_items_for_merge`.

**Config:** `INCORPORATION_GUARD_ENABLED=true` (default on)

### Files
| File | Change |
|------|--------|
| `services/incorporation_guard.py` | **new** |
| `services/named_policy_routing.py` | reuse `extract_named_policy_title_keys` |
| `graph/section_compare_nodes.py` | wire ~8 LOC |
| `prompts/section_compare.md` | tighten C1a (~3 lines) |
| `tests/test_incorporation_guard.py` | **new** 3 cases |

### Verify
- Code of Conduct §5.1 item → COMPLIANT or INCONCLUSIVE(info), not NON_COMPLIANT
- Real gap (contract contradicts policy threshold) → still NON_COMPLIANT

---

## C2 — Cross-section bundling

### Symptom
§2.1 `NON_COMPLIANT` — *no secure deletion*; §3.2 `COMPLIANT` — full deletion clause exists.

### Root cause

Compare receives **only primary section body** for §2.1. `resolve_related_sections()` links:
- survival ranges (`sections 3 through 7 survive`)
- explicit `section N` references

It does **not** link §2.1 → §3.x because the NDA has **no textual cross-reference** between confidentiality obligations and retention/destruction articles. Obligation is **document-level**, not citation-level.

### Production-grade fix (minimal)

**Category sibling bundling** — when primary section categories overlap substantive siblings, attach excerpts to compare context.

**Algorithm** (`resolve_category_siblings` in `section_cross_reference.py`, ~45 LOC):

```text
INPUT: section, all_sections, classifications_by_id
SPECIFIC_CATS = {confidentiality, data_retention, termination, privacy, security} - {general, compliance}

IF section.categories ∩ SPECIFIC_CATS is empty → return []

siblings = []
FOR other in all_sections:
  IF other.section_id == section.section_id: continue
  IF other.categories ∩ section.categories ∩ SPECIFIC_CATS is non-empty:
    siblings.append(other)  # cap 2 siblings, prefer 3.x for 2.x

Merge into RelatedSectionBundle.related (max 2 excerpts, 1200 chars each)
reason = "category_sibling:data_retention+confidentiality"
```

**Wire:**
- `section_retrieval_nodes.py` — when building `context_serialized`, call enhanced resolver OR
- `section_compare_nodes.py` — rebuild `related_by_section` from `contract_sections` + `categories_by_section` before compare

Prefer **compare_nodes** (no retrieval graph change) — read `state.contract_sections` + classification categories from bundles.

**Prompt** (`section_compare.md`, ~2 lines):
> When Related contract sections include category siblings, evaluate obligations **across the combined excerpts** — silence in the primary section is not a gap if a sibling section satisfies the policy requirement.

### Files
| File | Change |
|------|--------|
| `services/section_cross_reference.py` | add `resolve_category_siblings()` ~45 LOC |
| `graph/section_compare_nodes.py` | merge siblings into `related_by_section` ~15 LOC |
| `prompts/section_compare.md` | 2 lines |
| `tests/test_section_cross_reference.py` | 2 cases: 2.1 gets 3.2 excerpt |

### Verify
- Compare batch for §2.1 includes §3.2 excerpt in Related block
- After C3, no §2.1 NON_COMPLIANT on secure deletion when §3.2 COMPLIANT

---

## C3 — Finding dedupe across sections

### Symptom
Same topic: `Secure Deletion of Confidential Information` (§2.1) vs `Secure Deletion Requirements` (§3.2) — dedupe misses due to **label mismatch**.

### Root cause

`suppress_contradicted_non_compliant()` groups by `normalize_dimension_label()` exact string. Labels differ → treated as different dimensions → both kept.

### Production-grade fix (minimal)

**Topic clustering** before contradiction suppress (~35 LOC in `finding_dedupe.py`):

```text
DIMENSION_ALIASES = {
  "secure deletion": {"secure deletion", "secure deletion requirements", "deletion", "destruction"},
  "data principal rights": {"data principal rights", "data subject rights", "gdpr", "dpdpa"},
  ...
}

def dimension_topic_key(label: str) -> str:
  normalized = normalize_dimension_label(label)
  FOR topic, aliases in DIMENSION_ALIASES:
    IF any(alias in normalized for alias in aliases): return topic
  RETURN normalized

Group by topic_key instead of raw normalized label in suppress_contradicted_non_compliant
```

**Rule (unchanged logic, better grouping):**
- If any item in topic group is `COMPLIANT` with `contract_quote` → drop `NON_COMPLIANT` in same group
- Do **not** drop if NON_COMPLIANT cites a **different contract_quote** (material distinct gap)

**Config:** `FINDING_DEDUPE_TOPIC_CLUSTER=true` (default on)

### Files
| File | Change |
|------|--------|
| `services/finding_dedupe.py` | `dimension_topic_key()` + alias map ~35 LOC |
| `tests/test_finding_dedupe.py` | 1 case: mismatched labels same topic |

### Verify
- §2.1 + §3.2 secure deletion → only COMPLIANT remains

---

## C4 — Semantic equivalence guard

### Symptom
`Data Principal Rights — CRITICAL NON_COMPLIANT` while:
- Contract: *"subject to applicable legal retention requirements"*
- Policy: *"except where retention is otherwise required by law"*

### Root cause

Compare LLM treats **different phrasing** as missing requirement. No post-compare rule recognizes legal-equivalence pairs. Severity stays `critical` because model focuses on "policy mentions exceptions explicitly."

### Production-grade fix (minimal)

**Deterministic phrase-pair guard** — no LLM. Run after compare, before or with C3.

**New `equivalence_guard.py` (~50 LOC):**

```text
EQUIVALENCE_PAIRS = [
  ({"legal retention", "retention requirements", "applicable law"},
   {"required by law", "legal retention", "otherwise required"}),
  ({"legal hold", "litigation hold"},
   {"ongoing litigation", "audit", "investigation"}),
]

def apply_equivalence_guard(items) -> (items, downgraded_count):
  FOR each NON_COMPLIANT with severity critical/important:
    contract = lower(item.contract_quote + item.rationale)
    policy = lower(item.policy_quote + item.rationale)
    IF any_pair_matches(contract, policy, EQUIVALENCE_PAIRS):
      IF no_contradiction_signals(contract, policy):  # e.g. "shall not", "prohibited"
        → status COMPLIANT or INCONCLUSIVE, severity info
```

**Placement:** `prepare_compare_items_for_merge()` after `suppress_contradicted`, before `dedupe_compare_items`.

**Config:** `EQUIVALENCE_GUARD_ENABLED=true` (default on)

### Files
| File | Change |
|------|--------|
| `services/equivalence_guard.py` | **new** ~50 LOC |
| `services/finding_dedupe.py` | wire in `prepare_compare_items_for_merge` ~5 LOC |
| `config.py` | 1 flag |
| `tests/test_equivalence_guard.py` | **new** 2 cases (data principal rights, negative case)

### Verify
- Data Principal Rights finding → COMPLIANT/INCONCLUSIVE(info), not CRITICAL
- True gap (contract prohibits erasure entirely) → still NON_COMPLIANT

---

## C5 — Compare only coverage-passed hits

### Symptom
Compare invents violation from wrong policy evidence.

### Root cause
Compare LLM received off-topic policy chunks (retrieval issue). Even with cleaned retrieval, edge cases slip through.

### Status: ✅ Implemented

| Component | Location |
|-----------|----------|
| `apply_coverage_gate()` | `policy_coverage.py` |
| Wire before compare | `section_compare_nodes.py` lines 57–63 |
| IPC + skip compare | `validate_section_coverage()` |

### Hardening (optional, ~10 LOC)

Ensure `sections_with_policy` excludes sections where coverage gate emitted IPC (already true — hits cleared to `[]`).

Add ops metric: `compliance_stats.coverage_gate_ipc_count` in compare node return.

**No further code required** unless regression found.

---

## C6 — Re-run Xecurify assessment

### Purpose
Measure compare-layer impact after C1–C5.

### Procedure

```powershell
# Prereq: MCP restarted, policies re-indexed (A6–A7)
cd "d:\Ankit_legal\Legal\temp_java_sync"
python test_xecurify_policies.py
# Outputs: outputs/sync_result.json, outputs/review_result.json, outputs/review_assessment.json
python export_assessment.py  # if not auto-exported
```

### Record (before vs after)

| Metric | Baseline (pre-C) | Target (post-C) |
|--------|------------------|-----------------|
| NON_COMPLIANT sections | 9 | ≤5 |
| False positive violations | ~3–4 | ≤1 |
| CRITICAL violations | 7 | ≤3 |
| Code of Conduct false NC | 1 | 0 |
| §2.1 vs §3.2 deletion contradiction | yes | no |
| Data Principal Rights CRITICAL | 1 | 0 |
| Weighted alignment | ~48 | ≥65 |
| INCONCLUSIVE % | ~27% | <15% |

Save as `outputs/xecurify_nda_assessment_post_c.json`.

---

## Execution order (minimal PRs)

```text
PR-1 (C5 verify + C1 + C4)     ~120 LOC — highest false-positive impact
PR-2 (C2 + C3 hardening)       ~80 LOC  — cross-section contradictions
PR-3 (C6 ops + metrics)        — measurement only
```

**Do not** add new LLM calls. All guards are deterministic string/category logic.

---

## File change matrix

| File | C1 | C2 | C3 | C4 | C5 |
|------|----|----|----|----|-----|
| `incorporation_guard.py` | new | | | | |
| `equivalence_guard.py` | | | | new | |
| `section_cross_reference.py` | | edit | | | |
| `finding_dedupe.py` | | | edit | wire | |
| `section_compare_nodes.py` | wire | wire | | | metrics |
| `section_compare.md` | edit | edit | | | |
| `config.py` | +2 flags | | +1 flag | +1 flag | |
| tests (4 files) | new | edit | edit | new | |

**Total new production code:** ~140 LOC (excluding tests).  
**Compatible with project:** reuses `named_policy_routing`, `RelatedSectionBundle`, `prepare_compare_items_for_merge`, existing graph — no topology change.

---

## What NOT to do

| Avoid | Why |
|-------|-----|
| Second LLM call for incorporation/equivalence | Cost, latency, same reliability issues |
| Full contract in every compare batch | Token blow-up; siblings capped at 2 |
| Broad dimension alias list (50+ topics) | Start with 5–8 topics from Xecurify failures |
| Changing grounding rules in C4 | Equivalence runs pre-grounding on compare items |
| C2 in retrieval node | Keep compare context concerns in compare node |

---

## Acceptance criteria (Phase C complete)

- [ ] C1b: Code of Conduct acknowledgment → not NON_COMPLIANT
- [ ] C2: §2.1 compare prompt includes §3.2 excerpt when categories overlap
- [ ] C3: Secure deletion appears once (COMPLIANT wins)
- [ ] C4: Data Principal Rights not CRITICAL when legal-retention equivalence present
- [ ] C5: Coverage gate IPC warnings in review output; no compare on gated sections
- [ ] C6: `xecurify_nda_assessment_post_c.json` saved; metrics recorded in plan doc
- [ ] All unit tests green; `test_acme_nda_e2e` regression pass
