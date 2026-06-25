# Phase F — DevOps, Resilience & Repeatability (Implementation Plan)

**Scope:** F1–F5 only. Minimal diffs. Fix root causes, not symptoms.

**Goal:** Stable Mistral dev runs, named assessment artifacts after every review, dual-fixture regression (Xecurify + Acme), optional platform on `:8080`, and one README that anyone can follow end-to-end.

---

## Architecture (what Phase F touches)

```
Postgres :5435
    ↓
document-mcp :8003  ← sync / index / search
    ↓
dev_ui :8090  ← /api/sync-policies, /api/review-text
    ├─ direct → review_agent (in-process)
    └─ platform → legal_ai_platform :8080 /query  [optional, F4]
         ↓
outputs/
  review_result.json          (always, per review)
  review_assessment.json      (always, latest — F2 harden)
  {slug}_assessment.json      (named snapshot — F2 add)
  sync_result.json            (after sync)
```

---

## F1 — Mistral 429 handling (backoff + lower concurrency)

| | |
|---|---|
| **Symptom** | Review or sync fails mid-run with HTTP 429 from Mistral; Dev UI shows 500 or partial review with warnings. |
| **Root cause (dual path)** | (1) **Review path** already retries 429 in `llm_gateway.invoke_structured` with exponential backoff + `LLM_GLOBAL_CONCURRENCY` semaphore — but default **effective burst** still exceeds Mistral free/low-tier RPM when `SECTION_COMPARE_CONCURRENCY=2`, classify batches, and final-gap verify run concurrently on the same key. (2) **Sync/tag path** uses `document_core/llm/ingest_llm.py` — **no retry, no shared limiter**; policy re-index during sync can 429 independently of review. |
| **Production pattern** | Single global rate limiter + retry-with-jitter on all LLM HTTP exits; **concurrency profile** tuned per provider tier; surface `rate_limit_events` in logs/metrics so ops can dial down without code changes. |
| **Minimal fix** | **Config-only (0 code, immediate):** add “Mistral dev profile” to `temp_java_sync/.env` and `review/review_agent/.env.example`. **Small code (≤40 LOC):** mirror 429 retry in `ingest_llm.invoke_structured_json`. **Optional (≤10 LOC):** log `limiter.rate_limit_events` once per review in `report_node` metadata. |

### F1a — Mistral dev profile (operator + `.env.example`)

Add commented block; **uncomment for Mistral dev keys**:

```env
# --- Mistral dev (429-safe) ---
LLM_GLOBAL_CONCURRENCY=1
SECTION_COMPARE_CONCURRENCY=1
SECTION_CLASSIFY_BATCH_SIZE=1
LLM_RATE_LIMIT_MAX_RETRIES=5
LLM_RATE_LIMIT_BACKOFF_BASE_SECONDS=2
LLM_RATE_LIMIT_BACKOFF_MAX_SECONDS=60
```

| Variable | Default today | Mistral dev | Why |
|----------|---------------|-------------|-----|
| `LLM_GLOBAL_CONCURRENCY` | 2 | **1** | One in-flight LLM call globally |
| `SECTION_COMPARE_CONCURRENCY` | 2 | **1** | Compare batches don’t queue 2× behind semaphore |
| `SECTION_CLASSIFY_BATCH_SIZE` | 2 | **1** | Smaller classify bursts |
| `LLM_RATE_LIMIT_MAX_RETRIES` | 3 | **5** | More headroom after backoff |

**Files:** `review/review_agent/.env.example`, `temp_java_sync/.env.example` (already has `LLM_GLOBAL_CONCURRENCY=2` — change default comment + add profile block).

**Do not:** Add a second LLM client, change models, or disable final-gap verify.

### F1b — Ingest LLM 429 retry (code)

| | |
|---|---|
| **Root cause** | `ingest_llm.py` calls Mistral via raw `httpx` and `raise_for_status()` — 429 propagates immediately; sync aborts one policy mid-batch. |
| **Fix** | On `httpx.HTTPStatusError` with status 429 (and optionally 503): exponential backoff + jitter, max `LLM_RATE_LIMIT_MAX_RETRIES` (read from env, default 3). Reuse same formula as `llm_gateway` (copy constants, don’t import review_agent from document_core). |
| **Files** | `document_core/document_core/llm/ingest_llm.py` (~25 LOC) |
| **Verify** | Unit test with mocked 429→200; re-sync 7 policies without manual retry. |

### F1 — Verify

1. Set Mistral dev profile in `.env`.
2. Re-sync Xecurify policies → no 429 abort in MCP logs.
3. Run 30-section NDA review → completes; if 429 occurs, logs show `LLM rate limited (attempt …)` and recovery (not hard fail).
4. Optional: `report.metadata.rate_limit_events` > 0 only when throttled (informational).

---

## F2 — Auto-export assessment JSON after each Dev UI review

| | |
|---|---|
| **Symptom** | `outputs/xecurify_nda_assessment.json` is stale; latest run only updates `review_assessment.json` (or nothing if export threw). |
| **Root cause** | Export **already runs** in `dev_ui_server._run_review` (lines ~218–230) but: (1) **fixed output** `review_assessment.json` overwrites prior fixture runs; (2) **`except Exception: pass`** hides export failures; (3) **no slugged filename** from `contract_title` (manual CLI export needed for `xecurify_nda_assessment.json`); (4) API response doesn’t expose `assessment_path`. |
| **Production pattern** | Every review emits immutable `{run_id or slug}_assessment.json` + optional `latest` symlink/copy; export failure is **non-fatal to review** but **logged and returned** in response `warnings`. |
| **Minimal fix** | ~20 LOC in `dev_ui_server.py` + ~15 LOC helper in `export_assessment.py`. |

### F2 — Implementation

1. **`assessment_slug(title: str) -> str`** in `export_assessment.py`  
   - Lowercase, replace non-alnum with `_`, collapse `_`, trim.  
   - `"Mutual NDA - Xecurify / Recipient"` → `mutual_nda_xecurify_recipient`  
   - Cap length 64 chars.

2. **After review**, write **both**:
   - `outputs/review_assessment.json` (latest, unchanged behavior)
   - `outputs/{slug}_assessment.json` (named snapshot; e.g. `mutual_nda_xecurify_recipient_assessment.json` — or map known titles: Xecurify → `xecurify_nda_assessment.json` via optional `ASSESSMENT_SLUG_OVERRIDES` dict of 3 entries max)

3. **Replace silent `pass`** with:
   ```python
   except Exception as exc:
       logger.warning("assessment export failed: %s", exc)
       envelope.setdefault("warnings", []).append(f"assessment_export: {exc}")
   ```

4. **Return in envelope:** `assessment_paths: ["review_assessment.json", "xecurify_nda_assessment.json"]`

| File | Change |
|------|--------|
| `temp_java_sync/export_assessment.py` | `assessment_slug()`, optional override map |
| `temp_java_sync/dev_ui_server.py` | dual write, logging, `assessment_paths` in envelope |
| Platform path (`use_platform=True`) | same export block after envelope built (today platform branch skips export — **bug**; apply export once at end for both branches) |

**Do not:** Add DB persistence or new API endpoint.

### F2 — Verify

1. Dev UI review Xecurify NDA → `xecurify_nda_assessment.json` mtime updates.
2. Review Acme NDA → `acme_nda_assessment.json` updates (or slug from title).
3. Break `sync_result.json` intentionally → review succeeds, `warnings` contains `assessment_export: …`.

---

## F3 — ACME NDA regression test alongside Xecurify

| | |
|---|---|
| **Symptom** | Only `test_xecurify_policies.py` exercises Dev UI E2E; Acme clean-room NDA (`fixtures/acme_nda/`) never run in same harness → regressions in liability/indemnity compare missed. |
| **Root cause** | `review_agent/tests/test_acme_nda_e2e.py` is **in-process MCP mock** with compare LLM stub — not Dev UI, not real LLM, not named assessment export. Xecurify script doesn’t call `export_assessment` with stable names. |
| **Production pattern** | **Two golden fixtures** in CI/smoke: complex real-world (Xecurify) + controlled clean (Acme); both produce assessment JSON with threshold asserts on `finding_count`, `violations`, `confidence.weighted_alignment`. |
| **Minimal fix** | One new script + thin refactor of shared HTTP helpers (~80 LOC total). |

### F3 — Implementation

1. **Extract** `temp_java_sync/e2e_harness.py`:
   - `async def sync_policies(http, policies, **kwargs) -> dict`
   - `async def review_text(http, *, contract_text, contract_title, use_platform=False, **kwargs) -> dict`
   - `def export_named_assessment(slug: str) -> Path` — wraps `export_assessment(review_path, out_path=OUTPUTS / f"{slug}_assessment.json")`

2. **Add** `temp_java_sync/test_acme_nda_policies.py`:
   - Load `fixtures/acme_nda/acme_cloudvendor_nda.json`
   - Sync minimal policy set (2 policies: liability + indemnity — from fixture or inline refs matching `acme_fixtures.py` specs)
   - `POST /api/review-text` direct only (`use_platform=False`) — platform optional second pass like Xecurify
   - Export `acme_nda_assessment.json`
   - **Soft asserts** (print + exit 1): `finding_count <= 15`, no crash, `sync` tagger `llm` if key set

3. **Update** `test_xecurify_policies.py`:
   - Call `export_named_assessment("xecurify_nda")` after direct review
   - Add `--platform` flag (default off) so smoke works without F4

4. **Add** `temp_java_sync/run_regression_smoke.ps1`:
   ```powershell
   python test_xecurify_policies.py
   python test_acme_nda_policies.py
   ```

| File | Change |
|------|--------|
| `temp_java_sync/e2e_harness.py` | new, shared |
| `temp_java_sync/test_acme_nda_policies.py` | new |
| `temp_java_sync/test_xecurify_policies.py` | use harness + export |
| `temp_java_sync/run_regression_smoke.ps1` | new |

**Do not:** Duplicate full `test_acme_nda_e2e` MCP setup in temp_java_sync — keep that as unit/integration in review_agent.

### F3 — Verify

```powershell
cd "d:\Ankit_legal\Legal\temp_java_sync"
python test_acme_nda_policies.py
python test_xecurify_policies.py
# outputs/acme_nda_assessment.json + xecurify_nda_assessment.json fresh timestamps
```

---

## F4 — Start `legal_ai_platform` on `:8080` (Python 3.11–3.13)

| | |
|---|---|
| **Symptom** | Dev UI “Review via platform” → **503** `Platform not reachable at http://localhost:8080`. |
| **Root cause** | Platform is documented in README but **no start script** in `Legal ai/scripts/` (unlike document-mcp). Manual `pip install -e` + uvicorn is error-prone (path deps: `review-agent`, `document-core`, `deep_research_from_scratch`). Python **≥3.11** required (`pyproject.toml`); wrong venv → import failures. |
| **Production pattern** | One idempotent PS1: check Python version, install editable deps in order, load `.env`, bind `:8080`, pid file + `-Replace` / `-Status` parity with `start_document_mcp.ps1`. |
| **Minimal fix** | One script + `.env.example` pointer (~70 LOC PS1). |

### F4 — Implementation

**Add** `Legal/Legal ai/scripts/start_legal_ai_platform.ps1`:

| Step | Action |
|------|--------|
| 1 | `$LegalRoot = Split-Path (Split-Path $PSScriptRoot)` |
| 2 | Require `python --version` → 3.11 / 3.12 / 3.13 |
| 3 | `$env:PYTHONPATH` or ordered `pip install -e`: `document_core` → `review/review_agent` → `Legal_Ai_Research_Agent` → `legal_ai_platform[dev]` |
| 4 | Load `legal_ai_platform/.env` (copy from `.env.example` if missing — warn) |
| 5 | Set `DOCUMENT_SERVER_URL=http://localhost:8003`, `RETRIEVAL_SERVER_URL=http://localhost:8001` |
| 6 | `uvicorn legal_ai_platform.gateway.app:app --host 0.0.0.0 --port 8080` background + pid file `.legal_ai_platform.pid` |
| 7 | `-Replace` kills stale 8080 listener; `-Status` curls `/agents` |

**Prereqs (document in script header):** document-mcp up; retrieval-mcp optional for research-only.

| File | Change |
|------|--------|
| `Legal ai/scripts/start_legal_ai_platform.ps1` | new |
| `legal_ai_platform/.env.example` | ensure `DOCUMENT_SERVER_URL` documented |
| `temp_java_sync/run_dev_ui.ps1` | optional: print hint if `:8080` closed when starting Dev UI |

**Do not:** Dockerize platform in Phase F; don’t merge platform into dev_ui process.

### F4 — Verify

```powershell
cd "d:\Ankit_legal\Legal\Legal ai\scripts"
.\start_document_mcp.ps1 -Replace
.\start_legal_ai_platform.ps1 -Replace
curl http://localhost:8080/agents
# Dev UI → Review via platform → 200, via_platform: true
```

---

## F5 — Document full dev flow in README

| | |
|---|---|
| **Symptom** | Onboarding requires tribal knowledge; demo steps scattered across phase plans; `:9001` normalization mentioned in places though removed. |
| **Root cause** | `temp_java_sync/README.md` covers buttons but not **ordered baseline workflow**, assessment naming, Mistral profile, or platform start script (F4). |
| **Minimal fix** | One README section (~60 lines), link to this plan; no new docs tree. |

### F5 — README additions (`temp_java_sync/README.md`)

**New section: “Standard demo flow (sync → review → assessment)”**

1. Start Postgres + document-mcp (`start_postgres_podman.ps1`, `start_document_mcp.ps1 -Replace`)
2. Copy `.env`, set `LLM_API_KEY`; enable **Mistral dev profile** (F1) if 429
3. `.\run_dev_ui.ps1` → http://localhost:8090
4. **Sync** Xecurify policies → confirm `outputs/sync_result.json`, all `tagger: llm`
5. Paste NDA → **Run review** (direct) → `review_result.json` + `xecurify_nda_assessment.json`
6. Optional: `start_legal_ai_platform.ps1` → **Review via platform**
7. Regression: `.\run_regression_smoke.ps1` (F3)

**Table: output artifacts**

| File | When |
|------|------|
| `sync_result.json` | After sync |
| `review_result.json` | After every review |
| `review_assessment.json` | Latest assessment (UI parity) |
| `{slug}_assessment.json` | Named snapshot (F2) |
| `xecurify_nda_assessment.json` | Xecurify baseline / regression |
| `acme_nda_assessment.json` | Acme regression |

**Troubleshooting row:** 429 → F1 profile; 503 platform → F4 script; stale assessment → F2.

| File | Change |
|------|--------|
| `temp_java_sync/README.md` | add flow + artifact table + links |
| `review/plans/REVIEW_AGENT_IMPLEMENTATION_PLAN.md` | add Phase F row in status snapshot |

---

## Implementation order

| Order | Item | Type | Effort |
|-------|------|------|--------|
| 1 | **F1a** Mistral env profile | config | 5 min |
| 2 | **F2** dual assessment export + warnings | code | 30 min |
| 3 | **F1b** ingest_llm 429 retry | code | 45 min |
| 4 | **F4** start_legal_ai_platform.ps1 | script | 45 min |
| 5 | **F3** acme harness + smoke script | code | 1 h |
| 6 | **F5** README | docs | 30 min |

**Rationale:** F2 stops stale artifacts immediately; F1a unblocks long reviews without code; F1b fixes sync 429; F4/F3/F5 complete repeatability.

---

## Acceptance checklist (Phase F done)

- [x] Mistral dev profile documented; re-sync + 30-section review complete without manual restart
- [x] `ingest_llm` retries 429 (test passes)
- [x] Dev UI review updates `xecurify_nda_assessment.json` (or configured slug) every run
- [x] Export failures visible in response `warnings`, not silent
- [x] `test_acme_nda_policies.py` + `test_xecurify_policies.py` both pass; named assessments written
- [x] `start_legal_ai_platform.ps1 -Status` → `/agents` OK; platform review from Dev UI succeeds
- [x] README demo flow matches reality (no `:9001` requirement)

---

## Explicit non-goals (Phase F)

- Changing compare/retrieval/taxonomy logic (Phases B–E)
- CI/GitHub Actions wiring (local smoke only)
- Mistral paid-tier auto-detection
- Assessment JSON schema changes (Phase E `confidence` block already present)
