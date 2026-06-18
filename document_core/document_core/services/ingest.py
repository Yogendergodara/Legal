"""Ingest plain-text documents into the document store."""

from __future__ import annotations

from document_core.indexer.parent_child import build_parent_child_chunks
from document_core.parser.text_parser import parse_text_to_tree
from document_core.schemas.chunk import IngestRequest, IngestResult, StructureConfidence, new_document_id
from document_core.store.memory_store import get_store
from document_core.store.protocol import DocumentStore


async def ingest_document(
    request: IngestRequest,
    *,
    store: DocumentStore | None = None,
) -> IngestResult:
    """Layout-aware text ingest → section tree → parent/child index."""
    doc_store = store or get_store()
    document_id = request.document_id or new_document_id()
    warnings: list[str] = []

    tree = parse_text_to_tree(
        document_id=document_id,
        title=request.title,
        text=request.text,
    )

    if tree.structure_confidence == StructureConfidence.LOW:
        warnings.append("structure_confidence=low: headings may be incomplete")

    parents, children = build_parent_child_chunks(
        tree=tree,
        tenant_id=request.tenant_id,
        kind=request.kind,
        policy_type=request.policy_type,
        applies_to_contract_types=request.applies_to_contract_types,
        metadata={
            **request.metadata,
            "document_title": request.title,
            **({"categories": request.categories} if request.categories else {}),
        },
    )

    if not parents:
        warnings.append("no parent sections extracted; entire body stored as one section")

    doc_store.save_document(tree=tree, parents=parents, children=children)

    return IngestResult(
        document_id=document_id,
        tenant_id=request.tenant_id,
        kind=request.kind,
        title=request.title,
        parent_count=len(parents),
        child_count=len(children),
        structure_confidence=tree.structure_confidence,
        warnings=warnings,
    )
