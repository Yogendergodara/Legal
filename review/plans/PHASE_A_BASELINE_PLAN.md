# Phase A — Baseline & Unblock (Implementation Plan)

**Scope:** A1–A9 only. Minimal diffs. Fix root causes, not symptoms.

---

## A1 — Enable LLM policy tagger ✅

| | |
|---|---|
| **Root cause** | `document_core/.env` had no `LLM_API_KEY`; `CATEGORY_TAGGER_MODE=auto` fell back to keyword. |
| **Fix** | Add `LLM_API_KEY` + `LLM_BASE_URL`; set `CATEGORY_TAGGER_MODE=llm`. |
| **Files** | `document_core/.env`, `document_core/.env.example` |
| **Verify** | After re-sync: `sync_result.json` → `"tagger": "llm"` per policy. |

---

## A2 — Restart document-mcp ✅

| | |
|---|---|
| **Root cause** | MCP loads env only at process start; crashes leave port stale. |
| **Fix** | `.\scripts\start_document_mcp.ps1 -Replace` after any `document_core/.env` change. |
| **Verify** | `GET http://localhost:8003/health` → `status: ok` |

---

## A3 — Text parser duplicate section IDs ✅

| | |
|---|---|
| **Root cause** | `c. Low Severity (Level 3):` matched Roman heading `[IVXLC]+` (case-insensitive `c.`); `_derive_section_id` pulled `3` from `(Level 3)`, colliding with `3. Roles`. |
| **Fix** | (1) Roman headings require 2+ chars (`[IVXLC]{2,}`). (2) Prefer line-start numbers in `_derive_section_id`. (3) `_dedupe_section_ids()` safety net. |
| **Files** | `document_core/parser/text_parser.py`, `tests/test_text_parser.py` |
| **Verify** | `pytest document_core/tests/test_text_parser.py`; policy sync no `UniqueViolation` on Incident Response. |

---

## A4 — Retrieval crash (`str` has no `.get`) ✅

| | |
|---|---|
| **Root cause** | `contract_routing.topics` is `list[str]`; `multi_retrieval._query_for_attempt` called `.get()` on each topic. |
| **Fix** | Handle `str` and `dict` topic entries. |
| **Files** | `review_agent/services/multi_retrieval.py` |
| **Verify** | Re-run review; no `retrieval failed for section … 'str' object has no attribute 'get'` in warnings. |

---

## A5 — Tombstone orphan chunks ✅

| | |
|---|---|
| **Root cause** | `tombstone_policy_by_ref` set `index_status=deleted` but left `document_chunks` rows. |
| **Fix** | `DELETE FROM document_chunks` + `document_canonical` on tombstone. |
| **Files** | `document_core/store/pgvector_store.py` |
| **Verify** | Delete policy → `SELECT COUNT(*) FROM document_chunks WHERE document_id=…` → 0. |

---

## A6 — Re-index all policies 🔄 (operator)

| | |
|---|---|
| **Root cause** | DB still holds keyword-tagged chunks from before A1. |
| **Action** | Dev UI **Index policies** OR `python test_xecurify_policies.py` (sync only). |
| **Prereq** | A1 + A2 done; document-mcp healthy. |
| **Verify** | All policies in `sync_result.json` with `index_status_after: indexed`. |

---

## A7 — Verify LLM tagger active 🔄 (operator)

| | |
|---|---|
| **Root cause** | Same as A6 — need proof LLM path ran. |
| **Action** | Inspect `outputs/sync_result.json` → every policy `"tagger": "llm"`. |
| **If still `keyword`** | Check MCP logs for `category tagger LLM failed`; confirm `LLM_API_KEY` in env MCP loads. |

---

## A8 — Re-run Xecurify baseline ⏳ (operator)

| | |
|---|---|
| **Root cause** | `xecurify_nda_assessment.json` predates A3/A4/A6 fixes. |
| **Action** | Dev UI review OR full `test_xecurify_policies.py`; export assessment. |
| **Verify** | New `exported_at` timestamp; `retrieval failed` count = 0; record weighted score for Phase B comparison. |

---

## A9 — Re-index `UniqueViolation` on contract ingest ✅

| | |
|---|---|
| **Root cause (dual)** | **(1) Parser:** numbered exclusion lines like `1. Is or becomes publicly available…;` parsed as section heading `section_id=1`, colliding with `1. Definitions`. **(2) Store:** hash check ran outside write transaction → concurrent re-ingest could interleave DELETE/INSERT. |
| **Fix (minimal)** | **(1)** `_is_prose_list_line()` — skip numbered prose/list lines in `_match_heading`. **(2)** Single transaction for check + delete + insert in `save_document`. **(3)** `INSERT … ON CONFLICT DO UPDATE` on `document_chunks`. |
| **Files** | `text_parser.py`, `pgvector_store.py`, `tests/test_text_parser.py`, `tests/test_pgvector_save_document.py` |
| **Verify** | `pytest tests/test_text_parser.py tests/test_pgvector_save_document.py`; re-ingest same contract twice → no 500. |

---

## Execution order

```text
A1 ✅ → A2 ✅ → A3–A5 ✅ → A9 ✅ → A6 → A7 → A8
```

## Success criteria (Phase A complete)

- [ ] `sync_result.json`: all policies `tagger: llm`
- [ ] Policy sync: no 500 / `UniqueViolation`
- [ ] Contract re-review: no retrieval crash warnings
- [ ] `xecurify_nda_assessment.json` refreshed with baseline metrics
