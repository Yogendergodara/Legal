"""Persist crawled documents to S3 (raw HTML) and Postgres (clean text + FTS + embeddings)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from crawler.extraction import compute_content_hash
from db.models import WebDocument
from db.session import get_engine

logger = logging.getLogger(__name__)


def upload_raw_html_to_s3(
    content_hash: str,
    html: str,
    bucket: str,
    endpoint: str = "",
    access_key: str = "",
    secret_key: str = "",
) -> str:
    """Upload raw HTML to S3. Returns s3 URI."""
    try:
        import boto3

        client_kwargs: dict[str, Any] = {}
        if endpoint:
            client_kwargs["endpoint_url"] = endpoint
        if access_key:
            client_kwargs["aws_access_key_id"] = access_key
            client_kwargs["aws_secret_access_key"] = secret_key

        s3 = boto3.client("s3", **client_kwargs)
        key = f"raw/{content_hash}.html"
        s3.put_object(Bucket=bucket, Key=key, Body=html.encode("utf-8"), ContentType="text/html")
        uri = f"s3://{bucket}/{key}"
        logger.info("raw html uploaded", extra={"uri": uri, "content_hash": content_hash})
        return uri
    except Exception as exc:
        logger.warning(
            "s3 upload skipped",
            extra={"error": type(exc).__name__, "content_hash": content_hash},
        )
        return f"s3://{bucket}/raw/{content_hash}.html"


def document_exists_by_hash(session: Session, content_hash: str) -> bool:
    stmt = select(WebDocument.id).where(WebDocument.content_hash == content_hash).limit(1)
    return session.execute(stmt).scalar_one_or_none() is not None


def _get_embedding_sync(text: str) -> list[float]:
    from mcp.retrieval_server.embedding_service import _embed_sync
    return _embed_sync([text[:8000]])[0]


async def _get_embedding(text: str) -> list[float]:
    return await asyncio.to_thread(_get_embedding_sync, text)


def upsert_document(
    session: Session,
    *,
    url: str,
    canonical_url: str | None,
    source_id: int | None,
    title: str | None,
    clean_text: str,
    content_hash: str,
    published_at: str | None = None,
    embedding: list[float] | None = None,
) -> tuple[WebDocument, bool]:
    """Insert or update a web document. Returns (document, was_deduped)."""
    if document_exists_by_hash(session, content_hash):
        logger.info("doc deduped", extra={"content_hash": content_hash, "url": url})
        existing = session.execute(
            select(WebDocument).where(WebDocument.content_hash == content_hash).limit(1)
        ).scalar_one()
        return existing, True

    existing_url = session.execute(
        select(WebDocument).where(WebDocument.url == url).limit(1)
    ).scalar_one_or_none()

    pub_dt = None
    if published_at:
        try:
            pub_dt = datetime.fromisoformat(str(published_at).replace("Z", "+00:00"))
        except ValueError:
            pub_dt = None

    if existing_url:
        existing_url.title = title
        existing_url.clean_text = clean_text
        existing_url.content_hash = content_hash
        existing_url.canonical_url = canonical_url
        existing_url.crawled_at = datetime.now(timezone.utc)
        existing_url.published_at = pub_dt
        if embedding:
            existing_url.embedding = embedding
        session.execute(
            text("UPDATE web_documents SET tsv = to_tsvector('english', :txt) WHERE id = :id"),
            {"txt": clean_text or "", "id": existing_url.id},
        )
        logger.info("doc updated", extra={"url": url, "content_hash": content_hash})
        return existing_url, False

    doc = WebDocument(
        url=url,
        canonical_url=canonical_url,
        source_id=source_id,
        title=title,
        clean_text=clean_text,
        content_hash=content_hash,
        published_at=pub_dt,
        crawled_at=datetime.now(timezone.utc),
        embedding=embedding,
    )
    session.add(doc)
    session.flush()
    session.execute(
        text("UPDATE web_documents SET tsv = to_tsvector('english', :txt) WHERE id = :id"),
        {"txt": clean_text or "", "id": doc.id},
    )
    logger.info("doc upserted", extra={"url": url, "content_hash": content_hash})
    return doc, False


def create_tables(database_url: str) -> None:
    from db.models import Base
    engine = get_engine(database_url)
    Base.metadata.create_all(engine)
