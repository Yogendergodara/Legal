"""Canonical review output envelope for temp_java_sync harness (P3-10)."""

from __future__ import annotations

from typing import Any

from document_core.schemas.compliance import ReviewReport
from pydantic import BaseModel, Field

REVIEW_OUTPUT_SCHEMA_VERSION = "1.0"


class ReviewOutputEnvelope(BaseModel):
    schema_version: str = REVIEW_OUTPUT_SCHEMA_VERSION
    success: bool = True
    finding_count: int = 0
    findings: list[dict[str, Any]] = Field(default_factory=list)
    summary_markdown: str = ""
    artifact: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    discovered_policy_document_ids: list[str] = Field(default_factory=list)
    contract_document_id: str | None = None
    pipeline: str | None = None
    engine_diagnosis: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, Any] = Field(default_factory=dict)
    output: str = ""


def build_review_output_envelope(
    *,
    report: ReviewReport,
    state: dict[str, Any],
    contract_document_id: str | None = None,
) -> dict[str, Any]:
    """Single JSON shape for dev_ui, run_full_e2e, and run_review_only."""
    findings = [f.model_dump(mode="json") for f in report.findings]
    artifact = report.metadata.get("artifact") or {}
    engine_diagnosis = report.metadata.get("engine_diagnosis") or {}
    summary = report.summary_markdown or ""
    envelope = ReviewOutputEnvelope(
        finding_count=len(findings),
        findings=findings,
        summary_markdown=summary,
        output=summary,
        artifact=artifact,
        warnings=list(state.get("warnings") or []),
        discovered_policy_document_ids=[
            str(x) for x in (state.get("discovered_policy_document_ids") or [])
        ],
        contract_document_id=contract_document_id,
        pipeline=report.metadata.get("pipeline"),
        engine_diagnosis=engine_diagnosis,
        artifacts={
            "report": report.model_dump(mode="json"),
            "audit": artifact,
        },
    )
    return envelope.model_dump(mode="json")


def parse_findings_from_envelope(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Reader-side normalization — mirrors Dev UI parseReviewOutput."""
    if data.get("findings"):
        return list(data["findings"])
    report = (data.get("artifacts") or {}).get("report") or data.get("report") or {}
    return list(report.get("findings") or [])


def build_platform_review_payload(
    *,
    tenant_id: str,
    contract_document_id: str | None = None,
    contract_text: str | None = None,
    policy_document_ids: list[str] | None = None,
    contract_title: str,
    contract_type: str,
    policy_source: str = "indexed",
) -> dict[str, Any]:
    """Platform AgentRequest — query + contract text or document id."""
    payload: dict[str, Any] = {
        "query": f"Review {contract_title} for compliance",
        "task_type": "review",
        "tenant_id": tenant_id,
        "contract_title": contract_title,
        "contract_type": contract_type,
        "policy_source": policy_source,
    }
    if contract_document_id:
        payload["contract_document_id"] = contract_document_id
    if contract_text:
        payload["contract_text"] = contract_text
    if policy_document_ids:
        payload["policy_document_ids"] = [str(doc_id) for doc_id in policy_document_ids]
    return payload
