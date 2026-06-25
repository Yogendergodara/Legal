"""Ingest plain-text documents into the document store."""

from __future__ import annotations

from document_core.config import get_settings
from document_core.indexer.parent_child import CHUNK_VERSION, build_parent_child_chunks
from document_core.parser.structured_sections import sections_to_tree
from document_core.parser.text_parser import parse_text_to_tree
from document_core.schemas.chunk import DocumentKind, IngestRequest, IngestResult, StructureConfidence, new_document_id
from document_core.services.category_tagger import apply_keyword_tags, tag_policy_sections
from document_core.services.policy_profiler import profile_policy_tree
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

    if request.sections:
        tree = sections_to_tree(
            document_id=document_id,
            title=request.title,
            sections=request.sections,
        )
        warnings.append("structured sections ingest; heuristic parser skipped")
    else:
        tree = parse_text_to_tree(
            document_id=document_id,
            title=request.title,
            text=request.text,
        )

    if tree.structure_confidence == StructureConfidence.LOW:
        warnings.append(
            f"structure_confidence=low: {len(tree.sections)} section(s) from text; "
            "headings may be incomplete"
        )

    extra_meta: dict[str, object] = {}
    if request.kind == DocumentKind.POLICY:
        settings = get_settings()
        if settings.category_tagger_enabled:
            tree, extra_meta = await tag_policy_sections(
                tree,
                document_title=request.title,
                settings=settings,
            )
        else:
            apply_keyword_tags(tree, document_title=request.title)
            extra_meta = {"auto_tagged": True, "tagger": "keyword"}

        if settings.policy_profiler_enabled and settings.policy_profiler_mode != "off":
            profile, profiler_meta = await profile_policy_tree(
                tree,
                document_title=request.title,
                settings=settings,
            )
            extra_meta["catalog_profile"] = profile.model_dump(mode="json")
            extra_meta.update(profiler_meta)

    meta = {
        **request.metadata,
        "document_title": request.title,
        "chunk_version": CHUNK_VERSION,
        **extra_meta,
    }

    parents, children, skipped_empty = build_parent_child_chunks(
        tree=tree,
        tenant_id=request.tenant_id,
        kind=request.kind,
        policy_type=request.policy_type,
        metadata=meta,
    )

    if skipped_empty:
        warnings.append(f"skipped {skipped_empty} empty section(s); no parent chunks created")

    if not parents:
        warnings.append("no parent sections extracted; entire body stored as one section")

    if hasattr(doc_store, "save_document_async"):
        await doc_store.save_document_async(tree=tree, parents=parents, children=children)
    else:
        import asyncio
        await asyncio.to_thread(doc_store.save_document, tree=tree, parents=parents, children=children)

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
