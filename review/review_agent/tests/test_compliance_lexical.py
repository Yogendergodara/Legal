"""Unit tests for lexical compliance (legacy fallback)."""

from uuid import uuid4

from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk, RetrievalHit

from review_agent.services.compliance import compare_sections


def _chunk(text: str, *, kind: DocumentKind = DocumentKind.CONTRACT) -> IndexedChunk:
    doc_id = uuid4()
    return IndexedChunk(
        chunk_id="c1",
        document_id=doc_id,
        tenant_id="demo",
        kind=kind,
        chunk_role=ChunkRole.PARENT,
        section_id="s1",
        section_path="1",
        title="Section",
        text=text,
    )


def _hit(text: str, *, kind: DocumentKind) -> RetrievalHit:
    return RetrievalHit(parent_chunk=_chunk(text, kind=kind), score=1.0)


def test_lexical_non_compliant_low_overlap():
    policy = "Liability shall not exceed twelve months fees under any circumstance."
    contract = "Party A grants an unlimited perpetual license with no liability cap whatsoever."
    finding = compare_sections(
        dimension_id="liability",
        dimension_label="Liability",
        contract_hits=[_hit(contract, kind=DocumentKind.CONTRACT)],
        policy_hits=[_hit(policy, kind=DocumentKind.POLICY)],
    )
    assert finding is not None
    assert finding.status.value == "NON_COMPLIANT"


def test_lexical_compliant_high_overlap():
    text = (
        "Vendor liability shall not exceed the fees paid in the twelve months "
        "preceding the claim. Indirect damages are excluded."
    )
    finding = compare_sections(
        dimension_id="liability",
        dimension_label="Liability",
        contract_hits=[_hit(text, kind=DocumentKind.CONTRACT)],
        policy_hits=[_hit(text, kind=DocumentKind.POLICY)],
    )
    assert finding is not None
    assert finding.status.value == "COMPLIANT"


def test_lexical_insufficient_policy():
    finding = compare_sections(
        dimension_id="liability",
        dimension_label="Liability",
        contract_hits=[_hit("contract", kind=DocumentKind.CONTRACT)],
        policy_hits=[],
    )
    assert finding is not None
    assert finding.status.value == "INSUFFICIENT_POLICY_CONTEXT"
