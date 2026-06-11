"""Unified SQLAlchemy models for crawler index and retrieval MCP."""

from __future__ import annotations

from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


EMBEDDING_DIM = 384


class SeedSource(Base):
    __tablename__ = "seed_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    url_pattern: Mapped[str] = mapped_column(String(512), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    crawl_frequency: Mapped[str] = mapped_column(String(20), default="daily")
    robots_respected: Mapped[bool] = mapped_column(Boolean, default=True)
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    documents: Mapped[list["WebDocument"]] = relationship(back_populates="source")


class WebDocument(Base):
    __tablename__ = "web_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(String(2048), unique=True, nullable=False)
    canonical_url: Mapped[str | None] = mapped_column(String(2048))
    source_id: Mapped[int | None] = mapped_column(ForeignKey("seed_sources.id"))
    title: Mapped[str | None] = mapped_column(String(1024))
    clean_text: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    crawled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    tsv: Mapped[str | None] = mapped_column(TSVECTOR)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))

    source: Mapped[SeedSource | None] = relationship(back_populates="documents")

    __table_args__ = (
        Index("ix_web_documents_tsv", "tsv", postgresql_using="gin"),
    )


class TenantDocument(Base):
    __tablename__ = "tenant_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(String(512), nullable=False)
    title: Mapped[str] = mapped_column(String(1024), nullable=False)
    clean_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    tsv: Mapped[str | None] = mapped_column(TSVECTOR)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    doc_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "source_id", name="uq_tenant_source"),
        Index("ix_tenant_documents_tsv", "tsv", postgresql_using="gin"),
    )


class CitationEdge(Base):
    __tablename__ = "citation_edges"

    id: Mapped[int] = mapped_column(primary_key=True)
    from_source_id: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    to_source_id: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    from_source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    to_source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    citation_type: Mapped[str] = mapped_column(String(50), default="cites")
    edge_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB, default=dict)


class CrawlCache(Base):
    __tablename__ = "crawl_cache"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(String(2048), unique=True, nullable=False)
    etag: Mapped[str | None] = mapped_column(String(256))
    last_modified: Mapped[str | None] = mapped_column(String(128))
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )


def get_engine(database_url: str):
    return create_engine(database_url, pool_pre_ping=True)
