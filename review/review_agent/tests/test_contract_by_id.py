"""Tests for contract-by-ID review inputs and parser."""

from uuid import uuid4

import pytest
from document_core.schemas.chunk import ChunkRole, DocumentKind, IndexedChunk, IngestResult, StructureConfidence

from review_agent.graph.nodes import clause_detection_node, contract_parser_node
from review_agent.graph.review_inputs import validate_review_inputs


def test_validate_requires_contract_document_id():
    with pytest.raises(ValueError, match="contract_document_id is required"):
        validate_review_inputs(contract_document_id=None, policy_document_ids=["p1"])


def test_validate_requires_policy_document_ids():
    with pytest.raises(ValueError, match="policy_document_ids"):
        validate_review_inputs(
            contract_document_id=str(uuid4()),
            policy_document_ids=[],
            policy_scope="request",
        )


def test_validate_rejects_invalid_uuid():
    with pytest.raises(ValueError, match="invalid contract_document_id"):
        validate_review_inputs(
            contract_document_id="not-a-uuid",
            policy_document_ids=["p1"],
        )


def test_validate_accepts_ids():
    doc_id = str(uuid4())
    parsed, policy_ids, warnings = validate_review_inputs(
        contract_document_id=doc_id,
        policy_document_ids=["policy-a"],
    )
    assert parsed == doc_id
    assert policy_ids == ["policy-a"]
    assert warnings == []


@pytest.mark.asyncio
async def test_contract_parser_by_id_skips_ingest():
    doc_id = uuid4()
    section = IndexedChunk(
        chunk_id="c1",
        document_id=doc_id,
        tenant_id="demo",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id="1",
        section_path="1",
        title="Liability",
        text="Vendor liability shall not exceed fees paid.",
        metadata={"document_title": "Synced MSA"},
    )

    class _Client:
        ingest_called = False

        async def list_sections(self, _request):
            return [section]

        async def ingest_document(self, _request):
            _Client.ingest_called = True
            raise AssertionError("ingest should not be called")

    client = _Client()
    state = {
        "tenant_id": "demo",
        "contract_document_id": str(doc_id),
        "contract_title": "MSA",
    }
    result = await contract_parser_node(state, client)  # type: ignore[arg-type]
    assert not client.ingest_called
    assert result["ingest_result"].document_id == doc_id
    assert len(result["contract_sections"]) == 1


@pytest.mark.asyncio
async def test_clause_detection_skips_when_sections_present():
    section = IndexedChunk(
        chunk_id="c1",
        document_id=uuid4(),
        tenant_id="demo",
        kind=DocumentKind.CONTRACT,
        chunk_role=ChunkRole.PARENT,
        section_id="1",
        section_path="1",
        title="Liability",
        text="Vendor liability shall not exceed fees paid.",
    )

    class _Client:
        list_called = False

        async def list_sections(self, _request):
            _Client.list_called = True
            return []

    state = {
        "contract_sections": [section],
        "ingest_result": IngestResult(
            document_id=section.document_id,
            tenant_id="demo",
            kind=DocumentKind.CONTRACT,
            title="MSA",
            parent_count=1,
            child_count=0,
            structure_confidence=StructureConfidence.HIGH,
        ),
    }
    result = await clause_detection_node(state, _Client())  # type: ignore[arg-type]
    assert result["contract_sections"] == [section]
    assert not _Client.list_called


@pytest.mark.asyncio
async def test_contract_parser_missing_document_raises():
    doc_id = uuid4()

    class _Client:
        async def list_sections(self, _request):
            return []

    with pytest.raises(ValueError, match="not indexed"):
        await contract_parser_node(
            {"tenant_id": "demo", "contract_document_id": str(doc_id)},
            _Client(),  # type: ignore[arg-type]
        )
