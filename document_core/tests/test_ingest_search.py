from uuid import uuid4

import pytest

from document_core.parser.text_parser import parse_text_to_tree
from document_core.schemas.chunk import DocumentKind, IngestRequest, SearchRequest
from document_core.services.ingest import ingest_document
from document_core.services.search import search_contract
from document_core.store.pgvector_store import PgVectorDocumentStore
from tests.fixtures import SAMPLE_CONTRACT


def test_parse_numbered_sections():
    tree = parse_text_to_tree(
        document_id=uuid4(),
        title="MSA",
        text=SAMPLE_CONTRACT,
    )
    assert len(tree.sections) >= 2
    assert tree.structure_confidence.value in {"medium", "high"}


@pytest.mark.asyncio
async def test_subsection_search_returns_parent(store: PgVectorDocumentStore):
    tenant = "test-tenant"
    result = await ingest_document(
        IngestRequest(tenant_id=tenant, title="MSA", kind=DocumentKind.CONTRACT, text=SAMPLE_CONTRACT),
        store=store,
    )
    hits = await search_contract(
        SearchRequest(
            tenant_id=tenant,
            query="twelve months preceding the claim",
            document_id=result.document_id,
            top_k=3,
        ),
        store=store,
    )
    assert hits
    parent = hits[0].parent_chunk
    assert "12.2" in parent.section_id or "liability" in parent.title.lower()
    assert "twelve" in parent.text.lower()
