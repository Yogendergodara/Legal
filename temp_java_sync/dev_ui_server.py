#!/usr/bin/env python3
"""Dev UI backend — FastAPI :8090 (document-mcp sync + prod-style review)."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from bootstrap_env import load_env, setup_pythonpath

load_env()
setup_pythonpath()

from document_core.schemas.chunk import DocumentKind, SearchRequest  # noqa: E402
from document_core.schemas.taxonomy import STANDARD_POLICY_CATEGORIES  # noqa: E402
from review_agent.clients.document_client import DocumentMCPClient  # noqa: E402
from review_agent.config import get_settings as get_review_settings  # noqa: E402
from review_agent.errors import RecoverableError  # noqa: E402
from review_agent.graph.review_graph import run_review  # noqa: E402
from review_output import build_platform_review_payload, build_review_output_envelope  # noqa: E402
from export_assessment import assessment_slug, export_review_assessments  # noqa: E402
from sync_service import (  # noqa: E402
    OUTPUTS,
    fixture_contract_raw_text,
    resolve_tenant,
    save_sync_result,
    sync_fixture_policies,
    sync_policies_only,
)
ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
CONFIG_PATH = ROOT / "dev_ui_config.json"

app = FastAPI(title="Legal Review Dev UI", version="1.0")
app.mount("/static", StaticFiles(directory=str(WEB)), name="static")

_state: dict[str, Any] = {
    "document_server_url": os.environ.get("DOCUMENT_SERVER_URL", "http://localhost:8003"),
    "platform_url": os.environ.get("PLATFORM_URL", "http://localhost:8080"),
    "tenant_id": os.environ.get("E2E_TENANT_ID", "e2e-demo"),
    "last_sync": None,
}


class DevUiConfig(BaseModel):
    document_server_url: str = "http://localhost:8003"
    platform_url: str = "http://localhost:8080"
    tenant_id: str = "e2e-demo"


class ReviewRequestBody(BaseModel):
    contract_title: str = "Mutual NDA (Dev UI)"
    contract_type: str = "nda"
    use_platform: bool = False


class PolicySyncBody(BaseModel):
    policies: list[dict[str, Any]] = Field(default_factory=list)
    use_shared_tenant: bool = True
    replace_tenant_policies: bool = False
    tenant_id: str | None = None


class ReviewTextBody(BaseModel):
    query: str = "Review this contract against our policies"
    contract_text: str
    contract_title: str = "Contract"
    contract_type: str = "nda"
    use_platform: bool = False
    tenant_id: str | None = None


def _load_config() -> None:
    if CONFIG_PATH.is_file():
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        _state.update({k: data[k] for k in ("document_server_url", "platform_url", "tenant_id") if k in data})


def _save_config() -> None:
    CONFIG_PATH.write_text(
        json.dumps(
            {
                "document_server_url": _state["document_server_url"],
                "platform_url": _state["platform_url"],
                "tenant_id": _state["tenant_id"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _llm_configured() -> bool:
    return bool(os.environ.get("LLM_API_KEY") or os.environ.get("MISTRAL_API_KEY"))


def _document_client() -> DocumentMCPClient:
    return DocumentMCPClient(str(_state["document_server_url"]).rstrip("/"))


def _port_listener_count(port: int = 8003) -> int:
    try:
        out = subprocess.check_output(
            ["netstat", "-ano"],
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.CalledProcessError):
        return 0
    needle = f":{port}"
    return sum(1 for line in out.splitlines() if "LISTENING" in line and needle in line)


async def _run_review(
    *,
    tenant: str,
    contract_text: str,
    contract_title: str,
    contract_type: str,
    query: str,
    use_platform: bool,
    policy_source: str = "indexed",
) -> dict[str, Any]:
    text = contract_text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="contract_text is required")

    if not _llm_configured():
        raise HTTPException(
            status_code=400,
            detail="LLM_API_KEY not set — add it to review/review_agent/.env or temp_java_sync/.env",
        )

    if use_platform:
        payload = build_platform_review_payload(
            tenant_id=tenant,
            contract_text=text,
            contract_title=contract_title,
            contract_type=contract_type,
            policy_source=policy_source,
        )
        payload["query"] = query.strip() or payload["query"]
        platform_url = str(_state["platform_url"]).rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(600.0)) as http:
                response = await http.post(f"{platform_url}/query", json=payload)
        except httpx.ConnectError as exc:
            raise HTTPException(
                status_code=503,
                detail=(
                    f"Platform not reachable at {platform_url}. "
                    "Start legal_ai_platform on :8080 or use Run review (direct)."
                ),
            ) from exc
        if response.status_code >= 400:
            raise HTTPException(status_code=response.status_code, detail=response.text)
        body = response.json()
        if not body.get("success"):
            raise HTTPException(status_code=500, detail=body.get("error") or "platform review failed")
        report = (body.get("artifacts") or {}).get("report") or {}
        envelope = {
            "success": True,
            "finding_count": len(report.get("findings") or []),
            "findings": report.get("findings") or [],
            "summary_markdown": body.get("output") or "",
            "output": body.get("output") or "",
            "artifact": (body.get("artifacts") or {}).get("audit") or {},
            "artifacts": body.get("artifacts") or {},
            "discovered_policy_document_ids": report.get("metadata", {}).get(
                "discovered_policy_document_ids", []
            ),
            "pipeline": report.get("metadata", {}).get("pipeline"),
            "via_platform": True,
            "policy_source": policy_source,
            "contract_text_chars": len(text),
        }
    else:
        get_review_settings.cache_clear()
        os.environ["REVIEW_POLICY_SCOPE"] = policy_source
        client = _document_client()
        try:
            state = await run_review(
                client=client,
                tenant_id=tenant,
                contract_text=text,
                contract_title=contract_title,
                contract_type=contract_type,
                policy_document_ids=None,
                policy_scope=policy_source,
            )
        except (ValueError, RecoverableError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        report = state.get("report")
        if report is None:
            raise HTTPException(
                status_code=500,
                detail={"error": "no report", "warnings": state.get("warnings")},
            )
        contract_id = state.get("contract_document_id")
        envelope = build_review_output_envelope(
            report=report,
            state=state,
            contract_document_id=str(contract_id) if contract_id else None,
        )
        envelope["via_platform"] = False
        envelope["policy_source"] = policy_source
        envelope["contract_text_chars"] = len(text)

    envelope["tenant_id"] = tenant
    OUTPUTS.mkdir(exist_ok=True)
    review_json = json.dumps(envelope, indent=2)
    (OUTPUTS / "review_result.json").write_text(review_json, encoding="utf-8")
    slug = assessment_slug(contract_title)
    named_review = OUTPUTS / f"{slug}_review_result.json"
    if named_review.name != "review_result.json":
        named_review.write_text(review_json, encoding="utf-8")
        envelope["review_paths"] = ["review_result.json", named_review.name]
    else:
        envelope["review_paths"] = ["review_result.json"]
    try:
        tenant_sync = OUTPUTS / f"sync_{tenant}.json"
        sync_path = tenant_sync if tenant_sync.is_file() else OUTPUTS / "sync_result.json"
        envelope["assessment_paths"] = export_review_assessments(
            OUTPUTS / "review_result.json",
            contract_title=contract_title,
            sync_path=sync_path if sync_path.is_file() else None,
            test_type="dev_ui_review",
        )
    except Exception as exc:  # noqa: BLE001 — assessment export must not fail review
        logger.warning("assessment export failed: %s", exc)
        envelope.setdefault("warnings", []).append(f"assessment_export: {exc}")
    return envelope


@app.on_event("startup")
async def _startup() -> None:
    _load_config()


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


@app.get("/api/config")
async def get_config() -> dict[str, Any]:
    return {
        "document_server_url": _state["document_server_url"],
        "platform_url": _state["platform_url"],
        "tenant_id": _state["tenant_id"],
        "llm_configured": _llm_configured(),
    }


@app.post("/api/config")
async def post_config(body: DevUiConfig) -> dict[str, str]:
    _state["document_server_url"] = body.document_server_url.rstrip("/")
    _state["platform_url"] = body.platform_url.rstrip("/")
    _state["tenant_id"] = body.tenant_id
    _save_config()
    return {"status": "ok"}


@app.get("/api/taxonomy")
async def taxonomy() -> dict[str, Any]:
    return {"categories": sorted(STANDARD_POLICY_CATEGORIES)}


@app.get("/api/health")
async def health() -> dict[str, Any]:
    client = _document_client()
    document_mcp: dict[str, Any] = {"status": "error"}
    mcp_capabilities: list[str] = []
    try:
        document_mcp = await client.health()
        mcp_capabilities = list(document_mcp.get("capabilities") or [])
    except Exception as exc:  # noqa: BLE001
        document_mcp = {"status": "error", "detail": str(exc)}

    platform: dict[str, Any] = {"status": "skipped"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as http:
            r = await http.get(f"{_state['platform_url'].rstrip('/')}/health")
            if r.status_code == 200:
                platform = r.json()
            else:
                platform = {"status": "error", "code": r.status_code}
    except Exception as exc:  # noqa: BLE001
        platform = {"status": "unreachable", "detail": str(exc)}

    return {
        "status": "ok",
        "document_mcp": document_mcp,
        "mcp_capabilities": mcp_capabilities,
        "platform": platform,
        "port_listener_count": _port_listener_count(8003),
        "llm_configured": _llm_configured(),
        "normalization": {"status": "removed", "note": "Phase 36 — sync uses document-mcp directly"},
    }


def _friendly_error_detail(exc: Exception) -> str:
    detail = str(exc)
    lowered = detail.lower()
    if "uniqueviolation" in lowered or "duplicate key" in lowered:
        return (
            "Policy re-index failed (stale index rows). "
            "Enable Replace tenant policies before sync and try again."
        )
    if "index_policy" in lowered and "500" in lowered:
        return f"document-mcp index_policy failed: {detail}"
    return detail


@app.post("/api/sync")
async def api_sync() -> dict[str, Any]:
    """Index default NDA policy fixtures for the configured tenant."""
    tenant = _state["tenant_id"]
    client = _document_client()
    try:
        sync = await sync_fixture_policies(client, tenant_id=tenant)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=_friendly_error_detail(exc)) from exc
    save_sync_result(sync)
    _state["last_sync"] = sync
    return sync


@app.get("/api/fixture-contract")
async def api_fixture_contract() -> dict[str, str]:
    return {"contract_text": fixture_contract_raw_text()}


@app.post("/api/sync-policies")
async def api_sync_policies(body: PolicySyncBody) -> dict[str, Any]:
    tenant = (body.tenant_id or "").strip()
    if not tenant:
        tenant = resolve_tenant(shared=body.use_shared_tenant, configured=_state["tenant_id"])
    if not body.policies:
        raise HTTPException(status_code=400, detail="Add at least one policy with raw text")
    client = _document_client()
    try:
        sync = await sync_policies_only(
            client,
            tenant_id=tenant,
            policies=body.policies,
            replace_policies=body.replace_tenant_policies,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=_friendly_error_detail(exc)) from exc
    save_sync_result(sync)
    _state["last_sync"] = sync
    return sync


@app.post("/api/review")
async def api_review(body: ReviewRequestBody) -> dict[str, Any]:
    """Review fixture NDA raw text against indexed tenant policies."""
    sync = _state.get("last_sync")
    if sync is None:
        sync_path = OUTPUTS / "sync_result.json"
        if not sync_path.is_file():
            raise HTTPException(
                status_code=400,
                detail="Index policies first (fixture sync or sync-policies)",
            )
        sync = json.loads(sync_path.read_text(encoding="utf-8"))
    tenant = sync["tenant_id"]
    return await _run_review(
        tenant=tenant,
        contract_text=fixture_contract_raw_text(),
        contract_title=body.contract_title,
        contract_type=body.contract_type,
        query=f"Review {body.contract_title} for compliance",
        use_platform=body.use_platform,
    )


@app.post("/api/review-text")
async def api_review_text(body: ReviewTextBody) -> dict[str, Any]:
    tenant = (body.tenant_id or "").strip()
    if not tenant:
        last = _state.get("last_sync")
        tenant = last["tenant_id"] if last else _state["tenant_id"]
    return await _run_review(
        tenant=tenant,
        contract_text=body.contract_text,
        contract_title=body.contract_title,
        contract_type=body.contract_type,
        query=body.query,
        use_platform=body.use_platform,
    )


@app.post("/api/tombstone")
async def api_tombstone() -> dict[str, Any]:
    sync_path = OUTPUTS / "sync_result.json"
    if not sync_path.is_file():
        raise HTTPException(status_code=400, detail="Run sync first")
    sync = json.loads(sync_path.read_text(encoding="utf-8"))
    policies = sync.get("policies") or []
    if not policies:
        raise HTTPException(status_code=400, detail="No policies in sync result")

    victim = policies[0]
    policy_ref = victim["policy_ref"]
    tenant = sync["tenant_id"]
    client = _document_client()
    await client.delete_policy(tenant, policy_ref)

    search = await client.search_policy(
        SearchRequest(
            tenant_id=tenant,
            query="liability indemnity confidentiality",
            kind=DocumentKind.POLICY,
            top_k=20,
        )
    )
    deleted_id = str(victim["document_id"])
    deleted_in_hits = any(str(hit.parent_chunk.document_id) == deleted_id for hit in search)
    result = {
        "deleted_policy_ref": policy_ref,
        "deleted_document_id": deleted_id,
        "deleted_policy_in_hits": deleted_in_hits,
        "search_hit_count": len(search),
    }
    (OUTPUTS / "tombstone_result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


@app.post("/api/full-e2e")
async def api_full_e2e() -> dict[str, Any]:
    steps: list[dict[str, Any]] = []

    async def _step(name: str, coro) -> Any:
        try:
            result = await coro
            steps.append({"name": name, "ok": True})
            return result
        except Exception as exc:  # noqa: BLE001
            steps.append({"name": name, "ok": False, "error": str(exc)})
            raise

    try:
        sync = await _step("sync", api_sync())
        await _step(
            "review",
            _run_review(
                tenant=sync["tenant_id"],
                contract_text=fixture_contract_raw_text(),
                contract_title="Mutual NDA (Dev UI E2E)",
                contract_type="nda",
                query="Review Mutual NDA (Dev UI E2E) for compliance",
                use_platform=False,
            ),
        )
        tombstone = await _step("tombstone", api_tombstone())
        log = {"steps": steps, "tombstone": tombstone}
        (OUTPUTS / "e2e_log.json").write_text(json.dumps(log, indent=2), encoding="utf-8")
        return log
    except Exception:
        log = {"steps": steps}
        (OUTPUTS / "e2e_log.json").write_text(json.dumps(log, indent=2), encoding="utf-8")
        raise HTTPException(status_code=500, detail=log)


@app.get("/api/outputs/{filename}")
async def api_outputs(filename: str) -> Any:
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="invalid filename")
    path = OUTPUTS / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("DEV_UI_PORT", "8090"))
    uvicorn.run("dev_ui_server:app", host="0.0.0.0", port=port, reload=False)
