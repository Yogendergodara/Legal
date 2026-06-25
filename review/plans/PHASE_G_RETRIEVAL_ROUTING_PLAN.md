# Phase G — Retrieval & Routing Hardening (R1–R5)

**Scope:** Fix wrong-policy compare and inconsistent IPC for Xecurify NDA (`xecurify_nda_assessment.json`).  
**Principle:** Deterministic gates before compare LLM — minimal diffs, no graph changes.  
**Prerequisite:** Phase A–F baseline (fresh run: alignment 57.0, 3 NC, 73% uncertain).  
**Estimated diff:** ~120–160 LOC + tests (~80 LOC).  
**Out of scope:** Compare prompts (Phase I), boilerplate classify (Phase H), new playbooks (data).

---

## 1. Problem → root cause chain (verified in code)

| ID | Symptom (Xecurify) | Root cause (code) | Layer |
|----|-------------------|-------------------|-------|
| **R1** | §10.1 Governing Law → Incident Response Plan NC | (a) No tenant policy tagged `governing_law` → category filter **fallback returns all 5 docs** (`retrieval_category_filter_fallback=True`). (b) Hybrid search returns IR chunks. (c) `filter_hits_by_relevance()` **forces best hit when all scores &lt; min** (L79–81). (d) Compare runs with wrong evidence. | `multi_retrieval.py`, `retrieval_relevance.py`, `policy_coverage.py` |
| **R2** | §10.5 Notices → 8-hour incident NC | (a) Same fallback + keep-best-hit. (b) BM25/semantic matches **“notice” ≈ “notification”** in IR plan. (c) No **topic-family block** (`governing_law`/`notices` vs `incident_reporting`). (d) Compare prompt inflates gap (Phase I — not here). | Same + missing incompatibility guard |
| **R3** | Severability / boilerplate → IR compare | Same pipeline as R1/R2; boilerplate sections still **retrieve** when not `general`-only (Phase H deferred). | Same |
| **R4** | 5 sections IPC, §10.1/10.5 compared | **Inconsistent gates:** `single_off_topic_hit` only when `len(hits)==1` (`policy_coverage.py` L46–60). Multi-hit sections skip that check. Threshold split: retrieval `0.2` vs compare hit `0.35`. Weak title token (+0.2) can pass one gate, fail another. | `policy_coverage.py`, `config.py` |
| **R5** | §9 Liability, §4 IP, exclusions → useless IPC | **Data scope:** Xecurify fixture has **no** liability/IP playbooks. **Code amplifier:** `filter_doc_ids_by_category_overlap()` returns **all doc_ids when overlap set empty** (`policy_coverage.py` L192). Category filter fallback searches entire discovered set → irrelevant hits → IPC noise. | `policy_coverage.py`, fixture data |

**Not a root cause for R1–R4:** `section_policy_classify.md` (§10.1 correctly tagged `governing_law` via lexical). `contract_routing.md` (5/5 policies discovered OK).

---

## 2. Production-grade target behavior

```text
classify section → resolve indexed policies for categories
    → IF no indexed playbook for specific categories → IPC (no search)
    → ELSE retrieve within filtered doc_ids only
    → relevance filter (NO forced off-topic hit)
    → coverage gate (specific category overlap OR max score ≥ threshold)
    → compare LLM (only on-topic hits)
```

**Real-world pattern:** Enterprise CLM tools **do not compare** a clause to a playbook family that was never indexed for that topic; they mark “no policy coverage” instead of semantic nearest-neighbor.

---

## 3. Implementation tasks (minimal, ordered)

### G1 — Stop forcing off-topic hits into compare **(R1, R2, R3)**

| | |
|---|---|
| **Root cause** | `filter_hits_by_relevance()` L79–81: `if not relevant: return [best[0]], dropped` |
| **Fix** | When no hit `score >= min_score`, return **`([], hits)`** — empty relevant list. |
| **File** | `review_agent/services/retrieval_relevance.py` (~8 LOC) |
| **Config** | `retrieval_relevance_keep_best_fallback: bool = False` (default **false**). When `true`, restore legacy behavior for A/B only. |
| **Wire** | `multi_retrieval.py` L425–427: already `if relevant: hits = relevant` — empty relevant → zero hits (correct). |
| **Test** | `test_filter_returns_empty_when_all_off_topic()` — governing_law section, IR-only hit, assert `relevant == []`. |

---

### G2 — Require **specific category overlap** before compare **(R1, R2, R3, R4)**

| | |
|---|---|
| **Root cause** | Relevance score uses title tokens (+0.2) without requiring **taxonomy intersection** on non-broad categories. `governing_law` ∩ `incident_reporting` = ∅ but compare still runs. |
| **Fix** | Add `requires_specific_overlap(section_categories, hit) -> bool` in `retrieval_relevance.py`: if section has any **specific** category (not in `BROAD_POLICY_CATEGORIES`), at least one hit parent category must intersect section specific set. |
| **File** | `retrieval_relevance.py` (+25 LOC), `policy_coverage.py` `validate_section_coverage()` (+12 LOC) |
| **Logic** | After `filter_hits_by_relevance`, if section_specific non-empty and **no** hit passes overlap → `insufficient=True`, `reason="no_specific_category_overlap"`. |
| **Config** | `policy_coverage_require_specific_overlap: bool = True` (default on) |
| **Test** | §10.1 scenario: section `["governing_law"]`, hit IR `["incident_reporting","records_management"]` → insufficient. |

---

### G3 — Unify off-topic detection for **all hit counts** **(R4)**

| | |
|---|---|
| **Root cause** | `single_off_topic_hit` only when `len(hits)==1`. Multi-hit lists bypass strict veto. |
| **Fix** | Replace single-hit branch with: **`max_score = max(scores)`**; if `max_score < cfg.compare_hit_min_relevance_score` (default **0.35**, already in config) → insufficient, `reason="all_hits_below_relevance_floor"`. |
| **File** | `policy_coverage.py` (~15 LOC refactor) |
| **Config** | Reuse `compare_hit_min_relevance_score` — single threshold for retrieval gate + compare hit selection. Optionally raise `retrieval_relevance_min_score` default to **0.35** in `.env.example` only (keep code default 0.2 for backward compat, gate uses max of both). |
| **Test** | Two off-topic hits both score 0.25 → IPC (today would compare). |

---

### G4 — No category match → **do not search all policies** **(R1, R5 amplifier)**

| | |
|---|---|
| **Root cause** | `_resolve_filter_document_ids()`: when `list_policy_ids_by_categories` returns empty for `governing_law` / `liability`, **`retrieval_category_filter_fallback`** widens to full scope (`multi_retrieval.py` L170–175, L198–200). |
| **Fix** | When section has **specific** categories and `category_ids` empty after list call: return **`[]` filter doc ids** (skip search attempt) unless `retrieval_category_filter_fallback` and section categories are **`general` only**. Set bundle meta `skipped_reason: no_indexed_playbook_for_categories`. |
| **File** | `multi_retrieval.py` `_resolve_filter_document_ids()` (~20 LOC) |
| **Config** | `retrieval_category_filter_fallback: bool = True` → change default to **`False`** for production; document in `.env.example`. |
| **Compare path** | Zero hits → existing coverage/backfill → IPC with rationale **"No playbook indexed for categories: liability"** (not random IR text). |
| **Test** | Section categories `["liability"]`, catalog has no liability doc → `final_count=0`, no compare. |

---

### G5 — Remove overlap filter “return all” fallback **(R5)**

| | |
|---|---|
| **Root cause** | `filter_doc_ids_by_category_overlap()` L192: `return kept if kept else list(doc_ids)` |
| **Fix** | Return **`kept` only** (may be empty). Caller (`multi_retrieval`) already handles empty → G4 behavior. |
| **File** | `policy_coverage.py` (1 line) |
| **Test** | Overlap filter with no matches → `[]`, not full doc list. |

---

### G6 — Topic-family incompatibility veto **(R2 notices vs incident)**

| | |
|---|---|
| **Root cause** | Lexical/semantic conflation: contract **legal notice** vs policy **incident notification**. |
| **Fix** | Static map in `retrieval_relevance.py` (~30 LOC), checked in `score_hit_relevance` or post-score veto: |

```python
# section_specific → policy categories that MUST NOT compare
_INCOMPATIBLE: dict[frozenset[str], frozenset[str]] = {
    frozenset({"governing_law"}): frozenset({
        "incident_reporting", "breach_notification", "records_management", "business_continuity",
    }),
    # notices: detected via section title regex in gate, not taxonomy tag
}
```

| | |
|---|---|
| **Notices handling** | In `validate_section_coverage`, if section title matches `notices?|notice provisions?` (reuse `_BOILERPLATE_TITLE` from `section_gap_status.py` — import or shared constant) and hit doc categories intersect `incident_reporting|breach_notification` → insufficient, `reason="notice_vs_incident_mismatch"`. |
| **File** | `policy_coverage.py` + `retrieval_relevance.py` |
| **Test** | Title "Notices", IR hit → IPC, no compare. |

**Minimal alternative (if map feels heavy):** G2 + G4 alone may fix R2; add G6 only if Xecurify re-run still shows §10.5 NC.

---

### G7 — R5 data scope (no code required for correctness)

| | |
|---|---|
| **Root cause** | Fixture indexes 5 Xecurify policies — **none** tagged `liability`, `ip`, `indemnity`. |
| **Fix (ops)** | Document in `temp_java_sync/README.md`: sections 4, 9, exclusions IPC = **expected** until liability/IP playbooks synced. |
| **Optional code** | Preflight in `dev_ui_server` / harness: warn when section lexical categories ∩ catalog categories = ∅ for &gt; N sections. (~20 LOC, Phase G optional). |
| **Not a bug** | IPC with clear “no indexed playbook” is **correct** production behavior. |

---

## 4. Execution order

```text
G1 (remove keep-best) → G5 (overlap empty) → G4 (no fallback search)
    → G2 (specific overlap) → G3 (max-score floor) → G6 (notice/incident if needed)
    → G7 (docs) → re-run test_xecurify_policies.py
```

**Do not parallelize** — G1/G4/G5 change hit lists; tests after each.

---

## 5. Files touched (summary)

| File | Tasks | Δ LOC |
|------|-------|------|
| `review_agent/services/retrieval_relevance.py` | G1, G2, G6 | ~45 |
| `review_agent/services/policy_coverage.py` | G2, G3, G5, G6 | ~40 |
| `review_agent/services/multi_retrieval.py` | G4 | ~20 |
| `review_agent/config.py` | G1, G2, G4 flags | ~12 |
| `review_agent/.env.example` | document defaults | ~8 |
| `review_agent/tests/test_policy_coverage.py` | all | ~80 |
| `review_agent/tests/test_retrieval_relevance.py` | G1, G2 (new file) | ~50 |
| `temp_java_sync/README.md` | G7 | ~10 |

**No changes:** graph nodes, MCP, document_core taxonomy, compare prompt (Phase I).

---

## 6. Config defaults (production)

| Setting | Current | Phase G default |
|---------|---------|---------------|
| `RETRIEVAL_RELEVANCE_KEEP_BEST_FALLBACK` | (implicit true) | **false** |
| `RETRIEVAL_CATEGORY_FILTER_FALLBACK` | true | **false** |
| `POLICY_COVERAGE_REQUIRE_SPECIFIC_OVERLAP` | (new) | **true** |
| `RETRIEVAL_RELEVANCE_MIN_SCORE` | 0.2 | 0.2 (gate uses `max(0.2, compare_hit_min)`) |
| `COMPARE_HIT_MIN_RELEVANCE_SCORE` | 0.35 | 0.35 (used as floor in G3) |

---

## 7. Verification (Xecurify acceptance)

Run: `python test_xecurify_policies.py` with Dev UI :8090 + document-mcp.

| Check | Before | Target |
|-------|--------|--------|
| §10.1 compared to IR | NON_COMPLIANT | **IPC or no compare** |
| §10.5 incident 8-hour NC | critical NC | **IPC or no compare** |
| Severability → IR | INCONCLUSIVE | **IPC** |
| False NC count | 3 | **≤1** (§1 Sensitive Data may remain) |
| IPC from wrong-policy compare | mixed | **0** — IPC only for true no-playbook |
| `coverage gate` warnings | 5 sections | **includes 10.1, 10.5** |
| Weighted alignment | 57.0 | **≥65** (secondary) |

**Regression:** `pytest review_agent/tests/test_policy_coverage.py review_agent/tests/test_retrieval_relevance.py -q`

---

## 8. Risk / rollback

| Risk | Mitigation |
|------|------------|
| More IPC (less compare) | Intended — false NC worse than IPC for lawyers |
| Substantive sections lose hits | G4 only blocks when **specific** category has **zero** indexed docs; fallback stays for `general` |
| Rollback | Set `RETRIEVAL_RELEVANCE_KEEP_BEST_FALLBACK=true` + `RETRIEVAL_CATEGORY_FILTER_FALLBACK=true` |

---

## 9. Mapping to master plan

| Master | Phase G task |
|--------|----------------|
| Phase B2 (relevance gate) | G1 — complete B2 intent (remove keep-best) |
| Phase B3 (coverage validator) | G2, G3 — harden B3 |
| Phase B4 (category overlap) | G4, G5 — fix fallback semantics |
| Phase B5 (boilerplate) | G6 partial; full skip → Phase H |

---

## 10. Explicit non-goals (this phase)

- Editing `section_compare.md` (false NC after wrong pairing — Phase I)
- LLM classify for boilerplate (Phase H)
- Adding liability/IP policies to fixture (product data — G7 docs only)
- Prometheus / LLM call counters
