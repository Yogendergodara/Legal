"""Postgres full-text search over web_documents."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from db.session import get_engine

logger = logging.getLogger(__name__)


def _search_documents_sync(
    query: str,
    limit: int,
    database_url: str,
) -> list[dict[str, Any]]:
    engine = get_engine(database_url)
    results: list[dict[str, Any]] = []

    with Session(engine) as session:
        rows = session.execute(
            text("""
                SELECT url, title, LEFT(clean_text, 200) AS snippet,
                       ts_rank_cd(tsv, plainto_tsquery('english', :query)) AS score
                FROM web_documents
                WHERE tsv @@ plainto_tsquery('english', :query)
                ORDER BY score DESC
                LIMIT :limit
            """),
            {"query": query, "limit": limit},
        ).fetchall()

        for row in rows:
            results.append({
                "url": row.url,
                "title": row.title or "Untitled",
                "snippet": row.snippet or "",
                "description": row.snippet or "",
                "score": float(row.score) if row.score else 0.5,
                "engine": "legal-index",
            })

    logger.info("fts query complete", extra={"query": query[:200], "count": len(results)})
    return results


async def search_documents(
    query: str,
    limit: int = 10,
    database_url: str = "",
) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_search_documents_sync, query, limit, database_url)
