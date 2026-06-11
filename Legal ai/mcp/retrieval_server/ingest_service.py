"""Ingest tenant-scoped internal documents."""

from __future__ import annotations

import hashlib
import time
import uuid
from pathlib import Path

from sqlalchemy import text as sql_text

from db.models import TenantDocument
from db.session import get_session
from mcp.retrieval_server.config import Settings
from mcp.retrieval_server.embedding_service import embed_text
from mcp.retrieval_server.integrations import internal_file_store
from mcp.retrieval_server.logging_setup import get_logger, truncate

logger = get_logger(__name__)


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class IngestService:
    """Store and index tenant internal documents."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._file_root = (
            Path(settings.internal_storage_dir)
            if settings.internal_storage_dir
            else None
        )

    async def ingest_internal(
        self,
        tenant_id: str,
        title: str,
        doc_text: str,
        source_id: str | None = None,
        metadata: dict | None = None,
        request_id: str = "-",
    ) -> dict:
        start = time.perf_counter()
        doc_source_id = source_id or f"internal:{uuid.uuid4().hex[:12]}"
        content_hash = _content_hash(doc_text)

        logger.info(
            "ingest started",
            request_id=request_id,
            tenant_id=tenant_id,
            source_id=doc_source_id,
            title_truncated=truncate(title, 100),
            storage=self._settings.internal_storage,
        )

        if self._settings.internal_storage == "file":
            result = internal_file_store.ingest_document(
                tenant_id=tenant_id,
                title=title,
                doc_text=doc_text,
                content_hash=content_hash,
                source_id=source_id,
                metadata=metadata,
                root=self._file_root,
            )
            duration_ms = int((time.perf_counter() - start) * 1000)
            result["ingest_time_ms"] = duration_ms
            return result

        embedding = await embed_text(doc_text[:8000])

        with get_session(self._settings.database_url) as session:
            existing = session.query(TenantDocument).filter(
                TenantDocument.tenant_id == tenant_id,
                TenantDocument.content_hash == content_hash,
            ).first()
            if existing:
                logger.info(
                    "ingest deduped",
                    request_id=request_id,
                    tenant_id=tenant_id,
                    source_id=existing.source_id,
                )
                duration_ms = int((time.perf_counter() - start) * 1000)
                return {
                    "tenant_id": tenant_id,
                    "source_id": existing.source_id,
                    "title": existing.title,
                    "deduped": True,
                    "ingest_time_ms": duration_ms,
                }

            doc = TenantDocument(
                tenant_id=tenant_id,
                source_id=doc_source_id,
                title=title,
                clean_text=doc_text,
                content_hash=content_hash,
                embedding=embedding,
                doc_metadata=metadata or {},
            )
            session.add(doc)
            session.flush()
            session.execute(
                sql_text("UPDATE tenant_documents SET tsv = to_tsvector('english', :txt) WHERE id = :id"),
                {"txt": doc_text, "id": doc.id},
            )

        duration_ms = int((time.perf_counter() - start) * 1000)
        logger.info(
            "ingest completed",
            request_id=request_id,
            tenant_id=tenant_id,
            source_id=doc_source_id,
            duration_ms=duration_ms,
        )

        return {
            "tenant_id": tenant_id,
            "source_id": doc_source_id,
            "title": title,
            "deduped": False,
            "ingest_time_ms": duration_ms,
        }
