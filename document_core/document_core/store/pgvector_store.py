"""PostgreSQL + pgvector document store (production)."""

from __future__ import annotations

import json
import logging
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from document_core.embeddings.service import embed_documents, embed_query, embeddings_available
from document_core.store.content_hash import content_hash as compute_content_hash
from document_core.schemas.chunk import (
    ChunkRole,
    DocumentKind,
    DocumentTree,
    IndexedChunk,
    SearchRequest,
)
from document_core.schemas.registry import PolicyRegistryRecord
from document_core.search.lexical import score_query

logger = logging.getLogger(__name__)


def _document_categories(parents: list[IndexedChunk]) -> list[str]:
    from document_core.schemas.taxonomy import normalize_categories

    raw: list[str] = []
    for parent in parents:
        cats = (parent.metadata or {}).get("categories")
        if isinstance(cats, list):
            raw.extend(str(c) for c in cats)
    return normalize_categories(raw) or ["general"]


def _record_from_row(row: Any) -> PolicyRegistryRecord:
    meta = row.metadata if isinstance(row.metadata, dict) else {}
    return PolicyRegistryRecord(
        tenant_id=row.tenant_id,
        document_id=row.document_id,
        policy_ref=row.policy_ref or "",
        title=row.title,
        kind=row.kind,
        policy_type=row.policy_type,
        index_status=row.index_status,
        content_hash=row.content_hash,
        source=row.source or "upload",
        metadata=meta,
        indexed_at=row.indexed_at,
    )


def _chunk_from_row(row: Any) -> IndexedChunk:
    meta = row.metadata if isinstance(row.metadata, dict) else {}
    return IndexedChunk(
        chunk_id=row.chunk_id,
        document_id=row.document_id,
        tenant_id=row.tenant_id,
        kind=DocumentKind(row.kind),
        chunk_role=ChunkRole(row.chunk_role),
        parent_id=row.parent_id,
        section_id=row.section_id,
        section_path=row.section_path or row.section_id,
        title=row.title or row.section_id,
        text=row.text,
        context_text=row.context_text or row.text,
        policy_type=row.policy_type,
        metadata=meta,
    )


class PgVectorDocumentStore:
    """Persistent tenant-scoped document store with optional vector embeddings."""

    def __init__(self, database_url: str, *, hybrid_alpha: float = 0.5) -> None:
        self._engine: Engine = create_engine(
            database_url,
            pool_pre_ping=True,
            future=True,
            pool_size=10,
            max_overflow=20,
            pool_recycle=300,
        )
        self._hybrid_alpha = hybrid_alpha

    @property
    def engine(self) -> Engine:
        return self._engine

    def ping(self) -> bool:
        with self._engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True

    def save_document(
        self,
        *,
        tree: DocumentTree,
        parents: list[IndexedChunk],
        children: list[IndexedChunk],
    ) -> None:
        tenant_id = (
            parents[0].tenant_id
            if parents
            else children[0].tenant_id
        )
        document_id = tree.document_id
        kind = parents[0].kind if parents else children[0].kind
        title = tree.title
        policy_type = parents[0].policy_type if parents else children[0].policy_type
        base_meta = dict(parents[0].metadata if parents else (children[0].metadata if children else {}))
        metadata = {**base_meta, "categories": _document_categories(parents)}
        policy_ref = metadata.get("policy_ref")
        content_hash = compute_content_hash(
            tree.canonical_text,
            {
                "categories": metadata.get("categories") if metadata else None,
                "policy_type": policy_type,
            },
        )

        child_texts = [c.context_text or c.text for c in children]

        with self._engine.begin() as conn:
            existing_hash = conn.execute(
                text(
                    """
                    SELECT content_hash FROM policy_documents
                    WHERE tenant_id = :tenant_id AND document_id = :document_id
                    """
                ),
                {"tenant_id": tenant_id, "document_id": document_id},
            ).scalar()
            chunk_count = 0
            if existing_hash == content_hash:
                chunk_count = conn.execute(
                    text(
                        """
                        SELECT COUNT(*) FROM document_chunks
                        WHERE tenant_id = :tenant_id AND document_id = :document_id
                        """
                    ),
                    {"tenant_id": tenant_id, "document_id": document_id},
                ).scalar() or 0

            if existing_hash == content_hash and chunk_count > 0:
                conn.execute(
                    text(
                        """
                        UPDATE policy_documents
                        SET index_status = 'indexed', indexed_at = now(), last_verified_at = now()
                        WHERE tenant_id = :tenant_id AND document_id = :document_id
                        """
                    ),
                    {"tenant_id": tenant_id, "document_id": document_id},
                )
                logger.debug(
                    "skip re-index unchanged document tenant=%s document_id=%s",
                    tenant_id,
                    document_id,
                )
                return

            embeddings = embed_documents(child_texts) if children else []

            conn.execute(
                text(
                    """
                    DELETE FROM document_chunks
                    WHERE tenant_id = :tenant_id AND document_id = :document_id
                    """
                ),
                {"tenant_id": tenant_id, "document_id": document_id},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO policy_documents (
                        tenant_id, document_id, kind, title, policy_type,
                        applies_to_contract_types, policy_ref, content_hash,
                        source, metadata, index_status, last_verified_at
                    ) VALUES (
                        :tenant_id, :document_id, :kind, :title, :policy_type,
                        :applies_to, :policy_ref, :content_hash,
                        :source, CAST(:metadata AS jsonb), 'indexed', now()
                    )
                    ON CONFLICT (tenant_id, document_id) DO UPDATE SET
                        kind = EXCLUDED.kind,
                        title = EXCLUDED.title,
                        policy_type = EXCLUDED.policy_type,
                        applies_to_contract_types = EXCLUDED.applies_to_contract_types,
                        policy_ref = EXCLUDED.policy_ref,
                        content_hash = EXCLUDED.content_hash,
                        source = EXCLUDED.source,
                        metadata = EXCLUDED.metadata,
                        index_status = 'indexed',
                        indexed_at = now(),
                        last_verified_at = now()
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "document_id": document_id,
                    "kind": kind.value,
                    "title": title,
                    "policy_type": policy_type,
                    "applies_to": [],
                    "policy_ref": policy_ref,
                    "content_hash": content_hash,
                    "source": metadata.get("source", "upload") if metadata else "upload",
                    "metadata": json.dumps(metadata or {}),
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO document_canonical (tenant_id, document_id, canonical_text)
                    VALUES (:tenant_id, :document_id, :canonical_text)
                    ON CONFLICT (tenant_id, document_id) DO UPDATE SET
                        canonical_text = EXCLUDED.canonical_text
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "document_id": document_id,
                    "canonical_text": tree.canonical_text,
                },
            )

            all_chunks = parents + children
            child_index = 0
            for chunk in all_chunks:
                embedding_literal: str | None = None
                if chunk.chunk_role == ChunkRole.CHILD and embeddings is not None:
                    if child_index < len(embeddings):
                        embedding_literal = _vector_literal(embeddings[child_index])
                        child_index += 1

                conn.execute(
                    text(
                        """
                        INSERT INTO document_chunks (
                            tenant_id, document_id, chunk_id, chunk_role, parent_id,
                            section_id, section_path, title, text, context_text,
                            kind, policy_type, applies_to_contract_types,
                            embedding, tsv, metadata
                        ) VALUES (
                            :tenant_id, :document_id, :chunk_id, :chunk_role, :parent_id,
                            :section_id, :section_path, :title, :text, :context_text,
                            :kind, :policy_type, :applies_to,
                            CASE WHEN :embedding IS NULL THEN NULL ELSE CAST(:embedding AS vector) END,
                            to_tsvector('english', COALESCE(:context_text, :text)),
                            CAST(:metadata AS jsonb)
                        )
                        ON CONFLICT (tenant_id, document_id, chunk_id) DO UPDATE SET
                            chunk_role = EXCLUDED.chunk_role,
                            parent_id = EXCLUDED.parent_id,
                            section_id = EXCLUDED.section_id,
                            section_path = EXCLUDED.section_path,
                            title = EXCLUDED.title,
                            text = EXCLUDED.text,
                            context_text = EXCLUDED.context_text,
                            kind = EXCLUDED.kind,
                            policy_type = EXCLUDED.policy_type,
                            applies_to_contract_types = EXCLUDED.applies_to_contract_types,
                            embedding = EXCLUDED.embedding,
                            tsv = EXCLUDED.tsv,
                            metadata = EXCLUDED.metadata
                        """
                    ),
                    {
                        "tenant_id": chunk.tenant_id,
                        "document_id": chunk.document_id,
                        "chunk_id": chunk.chunk_id,
                        "chunk_role": chunk.chunk_role.value,
                        "parent_id": chunk.parent_id,
                        "section_id": chunk.section_id,
                        "section_path": chunk.section_path,
                        "title": chunk.title,
                        "text": chunk.text,
                        "context_text": chunk.context_text or chunk.text,
                        "kind": chunk.kind.value,
                        "policy_type": chunk.policy_type,
                        "applies_to": [],
                        "embedding": embedding_literal,
                        "metadata": json.dumps(chunk.metadata or {}),
                    },
                )

    def get_parents(self, tenant_id: str, document_id: UUID) -> list[IndexedChunk]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT * FROM document_chunks
                    WHERE tenant_id = :tenant_id AND document_id = :document_id
                      AND chunk_role = 'parent'
                    ORDER BY section_path
                    """
                ),
                {"tenant_id": tenant_id, "document_id": document_id},
            ).mappings()
            return [_chunk_from_row(row) for row in rows]

    def get_children(self, tenant_id: str, document_id: UUID) -> list[IndexedChunk]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT * FROM document_chunks
                    WHERE tenant_id = :tenant_id AND document_id = :document_id
                      AND chunk_role = 'child'
                    """
                ),
                {"tenant_id": tenant_id, "document_id": document_id},
            ).mappings()
            return [_chunk_from_row(row) for row in rows]

    def get_canonical_text(self, tenant_id: str, document_id: UUID) -> str | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT canonical_text FROM document_canonical
                    WHERE tenant_id = :tenant_id AND document_id = :document_id
                    """
                ),
                {"tenant_id": tenant_id, "document_id": document_id},
            ).first()
            return row[0] if row else None

    def list_documents(
        self,
        tenant_id: str,
        kind: DocumentKind | None = None,
    ) -> list[UUID]:
        query = """
            SELECT document_id FROM policy_documents
            WHERE tenant_id = :tenant_id
              AND index_status = 'indexed'
        """
        params: dict[str, Any] = {"tenant_id": tenant_id}
        if kind is not None:
            query += " AND kind = :kind"
            params["kind"] = kind.value
        with self._engine.connect() as conn:
            rows = conn.execute(text(query), params)
            return [row[0] for row in rows]

    def get_parent_by_section(
        self,
        tenant_id: str,
        document_id: UUID,
        section_id: str,
    ) -> IndexedChunk | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT * FROM document_chunks
                    WHERE tenant_id = :tenant_id AND document_id = :document_id
                      AND chunk_role = 'parent' AND section_id = :section_id
                    LIMIT 1
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "document_id": document_id,
                    "section_id": section_id,
                },
            ).mappings().first()
            return _chunk_from_row(row) if row else None

    def upsert_policy_registry(
        self,
        *,
        tenant_id: str,
        document_id: UUID,
        policy_ref: str,
        title: str,
        kind: str,
        policy_type: str | None,
        source: str,
        metadata: dict,
        index_status: Literal["pending", "indexed", "failed"],
    ) -> PolicyRegistryRecord:
        merged_meta = dict(metadata)
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO policy_documents (
                        tenant_id, document_id, kind, title, policy_type,
                        applies_to_contract_types, policy_ref, content_hash,
                        source, metadata, index_status
                    ) VALUES (
                        :tenant_id, :document_id, :kind, :title, :policy_type,
                        :applies_to, :policy_ref, NULL,
                        :source, CAST(:metadata AS jsonb), :index_status
                    )
                    ON CONFLICT (tenant_id, document_id) DO UPDATE SET
                        title = EXCLUDED.title,
                        policy_type = COALESCE(EXCLUDED.policy_type, policy_documents.policy_type),
                        applies_to_contract_types = EXCLUDED.applies_to_contract_types,
                        policy_ref = EXCLUDED.policy_ref,
                        source = EXCLUDED.source,
                        metadata = policy_documents.metadata || EXCLUDED.metadata,
                        index_status = CASE
                            WHEN policy_documents.index_status = 'indexed' THEN policy_documents.index_status
                            ELSE EXCLUDED.index_status
                        END
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "document_id": document_id,
                    "kind": kind,
                    "title": title,
                    "policy_type": policy_type,
                    "applies_to": [],
                    "policy_ref": policy_ref,
                    "source": source,
                    "metadata": json.dumps(merged_meta),
                    "index_status": index_status,
                },
            )
            row = conn.execute(
                text(
                    """
                    SELECT * FROM policy_documents
                    WHERE tenant_id = :tenant_id AND document_id = :document_id
                    """
                ),
                {"tenant_id": tenant_id, "document_id": document_id},
            ).mappings().one()
        return _record_from_row(row)

    def get_policy_by_ref(self, tenant_id: str, policy_ref: str) -> PolicyRegistryRecord | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT * FROM policy_documents
                    WHERE tenant_id = :tenant_id AND policy_ref = :policy_ref
                    LIMIT 1
                    """
                ),
                {"tenant_id": tenant_id, "policy_ref": policy_ref},
            ).mappings().first()
        return _record_from_row(row) if row else None

    def get_policy_registry_by_document_id(
        self, tenant_id: str, document_id: UUID
    ) -> PolicyRegistryRecord | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT * FROM policy_documents
                    WHERE tenant_id = :tenant_id AND document_id = :document_id
                    LIMIT 1
                    """
                ),
                {"tenant_id": tenant_id, "document_id": document_id},
            ).mappings().first()
        return _record_from_row(row) if row else None

    def tombstone_policy_by_ref(self, tenant_id: str, policy_ref: str) -> PolicyRegistryRecord | None:
        with self._engine.begin() as conn:
            row = conn.execute(
                text(
                    """
                    UPDATE policy_documents
                    SET index_status = 'deleted'
                    WHERE tenant_id = :tenant_id AND policy_ref = :policy_ref
                    RETURNING *
                    """
                ),
                {"tenant_id": tenant_id, "policy_ref": policy_ref},
            ).mappings().first()
            if row is None:
                return None
            document_id = row["document_id"]
            conn.execute(
                text(
                    """
                    DELETE FROM document_chunks
                    WHERE tenant_id = :tenant_id AND document_id = :document_id
                    """
                ),
                {"tenant_id": tenant_id, "document_id": document_id},
            )
            conn.execute(
                text(
                    """
                    DELETE FROM document_canonical
                    WHERE tenant_id = :tenant_id AND document_id = :document_id
                    """
                ),
                {"tenant_id": tenant_id, "document_id": document_id},
            )
        return _record_from_row(row)

    def list_policy_registry(
        self,
        tenant_id: str,
        *,
        kind: str | None = None,
        index_status: str | None = None,
    ) -> list[PolicyRegistryRecord]:
        query = "SELECT * FROM policy_documents WHERE tenant_id = :tenant_id"
        params: dict[str, Any] = {"tenant_id": tenant_id}
        if kind is not None:
            query += " AND kind = :kind"
            params["kind"] = kind
        if index_status is not None:
            query += " AND index_status = :index_status"
            params["index_status"] = index_status
        else:
            query += " AND index_status != 'deleted'"
        query += " ORDER BY title"
        with self._engine.connect() as conn:
            rows = conn.execute(text(query), params).mappings()
            return [_record_from_row(row) for row in rows]

    def set_policy_index_status(
        self,
        tenant_id: str,
        document_id: UUID,
        status: Literal["pending", "indexed", "failed"],
        *,
        error: str | None = None,
    ) -> None:
        with self._engine.begin() as conn:
            if error:
                conn.execute(
                    text(
                        """
                        UPDATE policy_documents
                        SET index_status = :status,
                            metadata = metadata || CAST(:err AS jsonb),
                            indexed_at = CASE WHEN :status = 'indexed' THEN now() ELSE indexed_at END
                        WHERE tenant_id = :tenant_id AND document_id = :document_id
                        """
                    ),
                    {
                        "tenant_id": tenant_id,
                        "document_id": document_id,
                        "status": status,
                        "err": json.dumps({"last_error": error}),
                    },
                )
            else:
                conn.execute(
                    text(
                        """
                        UPDATE policy_documents
                        SET index_status = :status,
                            indexed_at = CASE WHEN :status = 'indexed' THEN now() ELSE indexed_at END
                        WHERE tenant_id = :tenant_id AND document_id = :document_id
                        """
                    ),
                    {
                        "tenant_id": tenant_id,
                        "document_id": document_id,
                        "status": status,
                    },
                )

    def list_document_ids_by_categories(
        self,
        tenant_id: str,
        categories: list[str],
        *,
        contract_type: str | None = None,
        kind: DocumentKind = DocumentKind.POLICY,
    ) -> list[UUID]:
        if not categories:
            return []
        from document_core.config import get_settings

        settings = get_settings()
        params: dict[str, Any] = {
            "tenant_id": tenant_id,
            "kind": kind.value,
            "categories": categories,
        }
        stale_filter = ""
        if settings.policy_stale_days > 0:
            params["stale_days"] = settings.policy_stale_days
            stale_filter = (
                " AND last_verified_at > now() - make_interval(days => :stale_days)"
            )
        sql = f"""
            SELECT DISTINCT pd.document_id
            FROM policy_documents pd
            WHERE pd.tenant_id = :tenant_id AND pd.kind = :kind
              AND pd.index_status = 'indexed'
              AND (
                EXISTS (
                  SELECT 1 FROM jsonb_array_elements_text(
                    COALESCE(pd.metadata->'categories', '[]'::jsonb)
                  ) cat
                  WHERE cat = ANY(:categories)
                )
                OR EXISTS (
                  SELECT 1 FROM document_chunks pc
                  WHERE pc.tenant_id = pd.tenant_id
                    AND pc.document_id = pd.document_id
                    AND pc.chunk_role = 'parent'
                    AND EXISTS (
                      SELECT 1 FROM jsonb_array_elements_text(
                        COALESCE(pc.metadata->'categories', '[]'::jsonb)
                      ) c
                      WHERE c = ANY(:categories)
                    )
                )
              )
              {stale_filter}
        """
        with self._engine.connect() as conn:
            rows = conn.execute(text(sql), params).scalars().all()
        return list(rows)

    def search_children_fts(
        self,
        request: SearchRequest,
        document_ids: list[UUID],
    ) -> list[tuple[float, IndexedChunk]]:
        if not document_ids:
            return []
        doc_filter = ""
        params: dict[str, Any] = {
            "tenant_id": request.tenant_id,
            "query": request.query,
            "limit": request.top_k * 5,
        }
        if request.kind:
            params["kind"] = request.kind.value
            kind_filter = " AND kind = :kind"
        else:
            kind_filter = ""
        if len(document_ids) == 1:
            doc_filter = " AND document_id = :document_id"
            params["document_id"] = document_ids[0]
        else:
            doc_filter = " AND document_id = ANY(:document_ids)"
            params["document_ids"] = document_ids
        if request.policy_type:
            params["policy_type"] = request.policy_type
            policy_filter = " AND policy_type = :policy_type"
        else:
            policy_filter = ""

        sql = f"""
            SELECT *,
                ts_rank(tsv, plainto_tsquery('english', :query)) AS combined_score
            FROM document_chunks
            WHERE tenant_id = :tenant_id
              AND chunk_role = 'child'
              AND tsv @@ plainto_tsquery('english', :query)
              {kind_filter}
              {doc_filter}
              {policy_filter}
            ORDER BY combined_score DESC
            LIMIT :limit
        """
        scored: list[tuple[float, IndexedChunk]] = []
        with self._engine.connect() as conn:
            rows = conn.execute(text(sql), params).mappings()
            for row in rows:
                child = _chunk_from_row(row)
                scored.append((float(row["combined_score"]), child))
        return scored

    def search_children_scored(
        self,
        request: SearchRequest,
        document_ids: list[UUID],
        *,
        use_hybrid: bool,
    ) -> list[tuple[float, IndexedChunk]]:
        if not document_ids:
            return []

        if use_hybrid and embeddings_available():
            query_vec = embed_query(request.query)
            if query_vec:
                return self._search_hybrid(request, document_ids, query_vec)

        return self._search_lexical(request, document_ids)

    def _search_lexical(
        self,
        request: SearchRequest,
        document_ids: list[UUID],
    ) -> list[tuple[float, IndexedChunk]]:
        scored: list[tuple[float, IndexedChunk]] = []
        for document_id in document_ids:
            for child in self.get_children(request.tenant_id, document_id):
                if not _child_matches_filters(child, request):
                    continue
                score = score_query(request.query, child.context_text or child.text)
                if score > 0:
                    scored.append((score, child))
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[: request.top_k * 3]

    def _search_hybrid(
        self,
        request: SearchRequest,
        document_ids: list[UUID],
        query_vector: list[float],
    ) -> list[tuple[float, IndexedChunk]]:
        vec_lit = _vector_literal(query_vector)
        alpha = self._hybrid_alpha
        doc_filter = ""
        params: dict[str, Any] = {
            "tenant_id": request.tenant_id,
            "query": request.query,
            "vec": vec_lit,
            "alpha": alpha,
            "limit": request.top_k * 5,
        }
        if request.kind:
            params["kind"] = request.kind.value
            kind_filter = " AND kind = :kind"
        else:
            kind_filter = ""

        if len(document_ids) == 1:
            doc_filter = " AND document_id = :document_id"
            params["document_id"] = document_ids[0]
        else:
            doc_filter = " AND document_id = ANY(:document_ids)"
            params["document_ids"] = document_ids

        if request.policy_type:
            params["policy_type"] = request.policy_type
            policy_filter = " AND policy_type = :policy_type"
        else:
            policy_filter = ""

        sql = f"""
            SELECT *,
                (
                    :alpha * COALESCE(ts_rank(tsv, plainto_tsquery('english', :query)), 0)
                    + (1 - :alpha) * COALESCE(1 - (embedding <=> CAST(:vec AS vector)), 0)
                ) AS combined_score
            FROM document_chunks
            WHERE tenant_id = :tenant_id
              AND chunk_role = 'child'
              {kind_filter}
              {doc_filter}
              {policy_filter}
            ORDER BY combined_score DESC
            LIMIT :limit
        """
        scored: list[tuple[float, IndexedChunk]] = []
        with self._engine.connect() as conn:
            rows = conn.execute(text(sql), params).mappings()
            for row in rows:
                child = _chunk_from_row(row)
                scored.append((float(row["combined_score"]), child))
        return scored


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.8f}" for v in vec) + "]"


def _child_matches_filters(child: IndexedChunk, request: SearchRequest) -> bool:
    if request.kind and child.kind != request.kind:
        return False
    if request.policy_type and child.policy_type != request.policy_type:
        return False
    return True
