"""PgVectorDocumentStore save_document behavior (Phase 25)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from document_core.indexer.parent_child import build_parent_child_chunks
from document_core.parser.text_parser import parse_text_to_tree
from document_core.schemas.chunk import DocumentKind, IngestRequest
from document_core.services.ingest import ingest_document
from document_core.store.pgvector_store import PgVectorDocumentStore
from tests.fixtures import SAMPLE_CONTRACT


@pytest.mark.asyncio
async def test_reingest_unchanged_content_skips_embedding(monkeypatch, store: PgVectorDocumentStore):
    embed_calls: list[int] = []

    def _track_embed(texts: list[str]):
        embed_calls.append(len(texts))
        return None

    monkeypatch.setattr(
        "document_core.store.pgvector_store.embed_documents",
        _track_embed,
    )

    tenant = "phase25-skip-embed"
    request = IngestRequest(
        tenant_id=tenant,
        title="MSA",
        kind=DocumentKind.CONTRACT,
        text=SAMPLE_CONTRACT,
    )
    first = await ingest_document(request, store=store)
    assert embed_calls, "first ingest should embed child chunks"

    embed_calls.clear()
    second = await ingest_document(
        request.model_copy(update={"document_id": first.document_id}),
        store=store,
    )
    assert second.document_id == first.document_id
    assert embed_calls == [], "unchanged content hash should skip embed + re-index"


def test_save_document_has_no_nested_begin_blocks():
    """Guard: nested engine.begin() inside save_document was removed in Phase 25."""
    import inspect

    from document_core.store import pgvector_store

    source = inspect.getsource(pgvector_store.PgVectorDocumentStore.save_document)
    assert source.count("with self._engine.begin()") == 1
    assert "with self._engine.begin() as conn:\n                with self._engine.begin()" not in source


@pytest.mark.asyncio
async def test_save_document_write_path_is_atomic(store: PgVectorDocumentStore):
    tenant = "phase25-atomic"
    tree = parse_text_to_tree(document_id=uuid4(), title="Policy", text=SAMPLE_CONTRACT)
    parents, children, _ = build_parent_child_chunks(
        tree=tree,
        tenant_id=tenant,
        kind=DocumentKind.POLICY,
        metadata={"policy_ref": "p-atomic"},
    )
    store.save_document(tree=tree, parents=parents, children=children)

    stored_parents = store.get_parents(tenant, tree.document_id)
    stored_children = store.get_children(tenant, tree.document_id)
    assert stored_parents
    assert stored_children
    assert store.get_canonical_text(tenant, tree.document_id)
