"""Tests for ReviewArtifact builder (P5.1/P5.4)."""

from __future__ import annotations

import json
import uuid

from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk, RetrievalHit
from document_core.schemas.compliance import ComplianceFinding, ComplianceStatus, Severity
from review_agent.config import ReviewSettings
from review_agent.schemas.review_artifact import ARTIFACT_VERSION
from review_agent.schemas.section_compare import SectionCompareItem
from review_agent.schemas.section_retrieval import SectionRetrievalBundle
from review_agent.services.review_artifact import build_review_artifact


def _policy_hit(doc_id: uuid.UUID, section_id: str, text: str) -> RetrievalHit:
    return RetrievalHit(
        parent_chunk=IndexedChunk(
            chunk_id="p1",
            document_id=doc_id,
            tenant_id="demo",
            kind=DocumentKind.POLICY,
            chunk_role=ChunkRole.PARENT,
            section_id=section_id,
            section_path=section_id,
            title="Policy",
            text=text,
        ),
        score=0.91,
    )


def test_build_review_artifact_slim_retrieval_no_policy_text():
    doc_id = uuid.uuid4()
    bundle = SectionRetrievalBundle(
        section_id="s1",
        categories=["liability"],
        policy_hits=[_policy_hit(doc_id, "pol-1", "Full policy text must not appear in artifact.")],
        retrieval_meta={
            "attempts": [{"attempt": 1, "dense_count": 2, "final_count": 1}],
            "final_attempt": 1,
        },
    )
    state = {
        "tenant_id": "demo",
        "thread_id": "run-123",
        "contract_title": "MSA",
        "contract_document_id": str(uuid.uuid4()),
        "section_review_sections": [
            IndexedChunk(
                chunk_id="c1",
                document_id=uuid.uuid4(),
                tenant_id="demo",
                kind=DocumentKind.CONTRACT,
                chunk_role=ChunkRole.PARENT,
                section_id="s1",
                section_path="s1",
                title="Liability",
                text="Contract liability clause text.",
            ).model_dump(mode="json")
        ],
        "section_retrieval_by_id": {"s1": bundle.model_dump(mode="json")},
        "section_compare_items": [
            SectionCompareItem(
                section_id="s1",
                policy_document_id=str(doc_id),
                dimension_label="Cap",
                status=ComplianceStatus.NON_COMPLIANT,
                severity=Severity.CRITICAL,
                rationale="Cap below policy minimum.",
            ).model_dump(mode="json")
        ],
        "superseded_finding_ids": ["old-f1"],
        "compliance_stats": {"retrieval_retry_sections": 2},
        "final_verify_stats": {"gap_llm_sections": 1},
        "section_coverage": {"backfill_count": 1},
        "contract_routing": {"topics": ["liability"]},
        "discovered_policy_document_ids": [str(doc_id)],
    }
    artifact = build_review_artifact(state, settings=ReviewSettings())
    dumped = json.dumps(artifact.model_dump(mode="json"))
    assert ARTIFACT_VERSION in dumped
    assert artifact.ops.retrieval_retry_sections == 2
    assert artifact.ops.superseded_count == 1
    assert artifact.retrieval[0].retrieval_meta.get("attempts")
    assert artifact.retrieval[0].hits[0].document_id == str(doc_id)
    assert "Full policy text" not in dumped
    assert "Contract liability clause" not in dumped


def test_build_ops_from_findings():
    findings = [
        ComplianceFinding(
            finding_id="f1",
            dimension_id="s1:x",
            dimension_label="Cap",
            status=ComplianceStatus.NON_COMPLIANT,
            severity=Severity.CRITICAL,
            contract_section_id="s1",
            rationale="Below minimum cap per playbook.",
            grounded=False,
            metadata={"source": "playbook_compare", "grounding_failed": True},
        ),
        ComplianceFinding(
            finding_id="f2",
            dimension_id="s2:gap",
            dimension_label="Gap",
            status=ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
            severity=Severity.INFO,
            contract_section_id="s2",
            rationale="No policy retrieved for this section after expanded search.",
            grounded=True,
            metadata={"final_verify": "gap_llm"},
        ),
    ]
    state = {"tenant_id": "demo", "superseded_finding_ids": ["f-old"]}
    artifact = build_review_artifact(state, findings=findings)
    assert artifact.ops.ungrounded_count == 1
    assert artifact.ops.grounding_downgraded_count == 1
    assert artifact.ops.playbook_compare_count == 1
    assert len(artifact.gap_llm) == 1
    assert artifact.gap_llm[0].finding_id == "f2"


def test_build_ops_zero_hit_ids():
    state = {
        "tenant_id": "demo",
        "failed_sections": [
            {
                "section_id": "s2",
                "stage": "retrieve",
                "error_code": "retrieval_zero_hit",
                "message": "No policy hits after retrieval attempts",
            },
            {
                "section_id": "s9",
                "stage": "retrieve",
                "error_code": "retrieval_failed",
                "message": "timeout",
            },
        ],
        "compliance_stats": {"retrieval_zero_hit_sections": 1},
    }
    artifact = build_review_artifact(state)
    assert artifact.ops.degraded_section_count == 2
    assert artifact.ops.retrieval_zero_hit_section_ids == ["s2"]
    assert artifact.ops.degraded_section_count == len(artifact.degraded_sections)


def test_build_review_artifact_engine_diagnosis_mirror():
    diagnosis = {
        "schema_version": "1.0",
        "pipeline_mode": "section_first",
        "ipc_summary": {"section_ipc_pct": 0.0},
    }
    enriched_stats = {
        "sections_total": 2,
        "engine_diagnosis": diagnosis,
        "review_confidence": {"sections_total": 2},
    }
    state = {
        "tenant_id": "demo",
        "compliance_stats": {"sections_total": 2},
    }
    artifact = build_review_artifact(
        state,
        engine_diagnosis=diagnosis,
        compliance_stats=enriched_stats,
    )
    assert artifact.engine_diagnosis == diagnosis
    assert artifact.compliance_stats["sections_total"] == 2
    assert artifact.compliance_stats["engine_diagnosis"] == diagnosis
