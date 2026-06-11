"""Shared hybrid FTS + vector search queries."""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from db.session import get_engine


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(str(v) for v in vec) + "]"


def _search_web_sync(
    query: str,
    query_vec: list[float],
    limit: int,
    database_url: str,
    alpha: float,
) -> list[dict[str, Any]]:
    engine = get_engine(database_url)
    vec_lit = _vector_literal(query_vec)
    sql = text("""
        SELECT url, title, LEFT(clean_text, 200) AS snippet,
               ts_rank_cd(tsv, plainto_tsquery('english', :query)) AS fts_score,
               1 - (embedding <=> :vec::vector) AS sim_score
        FROM web_documents
        WHERE embedding IS NOT NULL
          AND (tsv @@ plainto_tsquery('english', :query) OR embedding IS NOT NULL)
        ORDER BY (:alpha * COALESCE(ts_rank_cd(tsv, plainto_tsquery('english', :query)), 0)
                + (1 - :alpha) * (1 - (embedding <=> :vec::vector))) DESC
        LIMIT :limit
    """)
    with Session(engine) as session:
        rows = session.execute(
            sql, {"query": query, "vec": vec_lit, "limit": limit, "alpha": alpha}
        ).fetchall()
    return [
        {
            "source_id": r.url,
            "source_type": "web",
            "title": r.title or "Untitled",
            "text_snippet": r.snippet or "",
            "url": r.url,
            "score": float(max(r.fts_score or 0, r.sim_score or 0)),
            "similarity_score": float(r.sim_score or 0),
        }
        for r in rows
    ]


def _search_tenant_sync(
    query: str,
    query_vec: list[float],
    tenant_id: str,
    limit: int,
    database_url: str,
    alpha: float,
) -> list[dict[str, Any]]:
    engine = get_engine(database_url)
    vec_lit = _vector_literal(query_vec)
    sql = text("""
        SELECT source_id, title, LEFT(clean_text, 200) AS snippet,
               ts_rank_cd(tsv, plainto_tsquery('english', :query)) AS fts_score,
               1 - (embedding <=> :vec::vector) AS sim_score
        FROM tenant_documents
        WHERE tenant_id = :tenant_id AND embedding IS NOT NULL
        ORDER BY (:alpha * COALESCE(ts_rank_cd(tsv, plainto_tsquery('english', :query)), 0)
                + (1 - :alpha) * (1 - (embedding <=> :vec::vector))) DESC
        LIMIT :limit
    """)
    with Session(engine) as session:
        rows = session.execute(
            sql,
            {"query": query, "vec": vec_lit, "tenant_id": tenant_id, "limit": limit, "alpha": alpha},
        ).fetchall()
    return [
        {
            "source_id": r.source_id,
            "source_type": "internal",
            "title": r.title or "Untitled",
            "text_snippet": r.snippet or "",
            "url": "",
            "score": float(max(r.fts_score or 0, r.sim_score or 0)),
            "similarity_score": float(r.sim_score or 0),
        }
        for r in rows
    ]


def _semantic_web_sync(
    query_vec: list[float],
    limit: int,
    database_url: str,
    threshold: float,
) -> list[dict[str, Any]]:
    engine = get_engine(database_url)
    vec_lit = _vector_literal(query_vec)
    sql = text("""
        SELECT url, title, LEFT(clean_text, 200) AS snippet,
               1 - (embedding <=> :vec::vector) AS similarity_score
        FROM web_documents
        WHERE embedding IS NOT NULL
          AND (1 - (embedding <=> :vec::vector)) >= :threshold
        ORDER BY embedding <=> :vec::vector
        LIMIT :limit
    """)
    with Session(engine) as session:
        rows = session.execute(
            sql, {"vec": vec_lit, "limit": limit, "threshold": threshold}
        ).fetchall()
    return [
        {
            "source_id": r.url,
            "source_type": "web",
            "title": r.title or "Untitled",
            "text_snippet": r.snippet or "",
            "similarity_score": float(r.similarity_score),
        }
        for r in rows
    ]


def _semantic_tenant_sync(
    query_vec: list[float],
    tenant_id: str,
    limit: int,
    database_url: str,
    threshold: float,
) -> list[dict[str, Any]]:
    engine = get_engine(database_url)
    vec_lit = _vector_literal(query_vec)
    sql = text("""
        SELECT source_id, title, LEFT(clean_text, 200) AS snippet,
               1 - (embedding <=> :vec::vector) AS similarity_score
        FROM tenant_documents
        WHERE tenant_id = :tenant_id AND embedding IS NOT NULL
          AND (1 - (embedding <=> :vec::vector)) >= :threshold
        ORDER BY embedding <=> :vec::vector
        LIMIT :limit
    """)
    with Session(engine) as session:
        rows = session.execute(
            sql,
            {"vec": vec_lit, "tenant_id": tenant_id, "limit": limit, "threshold": threshold},
        ).fetchall()
    return [
        {
            "source_id": r.source_id,
            "source_type": "internal",
            "title": r.title or "Untitled",
            "text_snippet": r.snippet or "",
            "similarity_score": float(r.similarity_score),
        }
        for r in rows
    ]


async def hybrid_search_web(
    query: str, query_vec: list[float], limit: int, database_url: str, alpha: float
) -> list[dict[str, Any]]:
    return await asyncio.to_thread(
        _search_web_sync, query, query_vec, limit, database_url, alpha
    )


async def hybrid_search_tenant(
    query: str,
    query_vec: list[float],
    tenant_id: str,
    limit: int,
    database_url: str,
    alpha: float,
) -> list[dict[str, Any]]:
    return await asyncio.to_thread(
        _search_tenant_sync, query, query_vec, tenant_id, limit, database_url, alpha
    )


async def semantic_search_web(
    query_vec: list[float], limit: int, database_url: str, threshold: float
) -> list[dict[str, Any]]:
    return await asyncio.to_thread(
        _semantic_web_sync, query_vec, limit, database_url, threshold
    )


async def semantic_search_tenant(
    query_vec: list[float],
    tenant_id: str,
    limit: int,
    database_url: str,
    threshold: float,
) -> list[dict[str, Any]]:
    return await asyncio.to_thread(
        _semantic_tenant_sync, query_vec, tenant_id, limit, database_url, threshold
    )
