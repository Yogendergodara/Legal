"""Tests for hybrid compliance pre-screen gate."""

from __future__ import annotations

from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk, RetrievalHit
from uuid import uuid4

from document_core.schemas.compliance import ComplianceStatus
from review_agent.config import ReviewSettings
from review_agent.schemas.alignment import AlignmentRecord
from review_agent.schemas.review_category import ReviewCategory
from review_agent.services.compliance_prescreen import run_prescreen


def _parent_chunk(text: str, *, section_id: str = "s1") -> IndexedChunk:
    doc_id = uuid4()
    return IndexedChunk(
        chunk_id=f"{doc_id}:{section_id}",
        document_id=doc_id,
        tenant_id="demo",
        kind=DocumentKind.POLICY,
        chunk_role=ChunkRole.PARENT,
        section_id=section_id,
        section_path=section_id,
        title=section_id,
        text=text,
        context_text=text,
    )


def _hit(text: str, score: float = 0.9) -> RetrievalHit:
    return RetrievalHit(parent_chunk=_parent_chunk(text), score=score)


def test_prescreen_defers_mid_band(monkeypatch):
    category = ReviewCategory(
        category_id="c1",
        label="Liability",
        search_queries=["liability"],
        source="policy_section",
    )
    policy_text = "policy text about liability caps"
    contract_text = "contract text with partial liability language"

    def _fake_score(query: str, document_text: str) -> float:
        if query == document_text:
            return 1.0
        return 0.12

    monkeypatch.setattr(
        "review_agent.services.compliance_prescreen.score_query",
        _fake_score,
    )

    policy_hits = [_hit(policy_text)]
    contract_hits = [_hit(contract_text, score=0.8)]
    alignment = AlignmentRecord(
        category_id="c1",
        combined_score=0.85,
        policy_text_excerpt=policy_text,
        contract_text_excerpt=contract_text,
        retrieval_method="exact",
    )
    settings = ReviewSettings(
        compliance_mode="hybrid",
        compliance_prescreen_enabled=True,
        compliance_retrieval_score_min=0.1,
    )
    outcome = run_prescreen(
        [category],
        {"c1": policy_hits},
        {"c1": contract_hits},
        {"c1": alignment},
        {"c1": {}},
        settings=settings,
    )
    assert outcome.resolved == []
    assert len(outcome.deferred) == 1


def test_prescreen_resolves_no_policy():
    category = ReviewCategory(
        category_id="c1",
        label="Liability",
        search_queries=["liability"],
        source="policy_section",
    )
    alignment = AlignmentRecord(category_id="c1", combined_score=0.0)
    outcome = run_prescreen(
        [category],
        {"c1": []},
        {"c1": []},
        {"c1": alignment},
        {"c1": {}},
    )
    assert len(outcome.resolved) == 1
    assert outcome.resolved[0].status == ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT
    assert outcome.deferred == []


def test_prescreen_disabled_defers_all():
    category = ReviewCategory(
        category_id="c1",
        label="Liability",
        search_queries=["liability"],
        source="policy_section",
    )
    settings = ReviewSettings(compliance_prescreen_enabled=False)
    outcome = run_prescreen(
        [category],
        {},
        {},
        {"c1": AlignmentRecord(category_id="c1")},
        {},
        settings=settings,
    )
    assert outcome.resolved == []
    assert len(outcome.deferred) == 1
