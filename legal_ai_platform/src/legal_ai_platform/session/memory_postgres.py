"""Postgres-backed long-term memory (FTS; optional embedding column unused in v1)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


class PostgresMemoryStore:
    """Tenant-scoped durable facts in platform_memory."""

    def __init__(self, database_url: str) -> None:
        self._engine: Engine = create_engine(database_url, future=True)

    def search(self, tenant_id: str, queries: list[str], *, limit: int = 5) -> list[dict[str, Any]]:
        if not queries:
            return []

        seen: set[str] = set()
        hits: list[dict[str, Any]] = []
        with self._engine.connect() as conn:
            for query in queries:
                q = (query or "").strip()
                if not q:
                    continue
                rows = conn.execute(
                    text(
                        """
                        SELECT title, content
                        FROM platform_memory
                        WHERE tenant_id = :tenant_id
                          AND to_tsvector('english', title || ' ' || content)
                              @@ plainto_tsquery('english', :query)
                        ORDER BY created_at DESC
                        LIMIT :limit
                        """
                    ),
                    {"tenant_id": tenant_id, "query": q, "limit": limit},
                ).mappings().all()
                for row in rows:
                    key = row["title"]
                    if key in seen:
                        continue
                    seen.add(key)
                    hits.append({"name": row["title"], "content": row["content"]})
                    if len(hits) >= limit:
                        return hits
        return hits[:limit]

    def save(
        self,
        *,
        title: str,
        content: str,
        hook: str,
        tenant_id: str,
        thread_id: str | None,
        agent: str,
    ) -> dict[str, Any]:
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO platform_memory
                        (tenant_id, thread_id, agent, title, content, hook)
                    VALUES
                        (:tenant_id, :thread_id, :agent, :title, :content, :hook)
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "thread_id": thread_id,
                    "agent": agent,
                    "title": title,
                    "content": content,
                    "hook": hook or title,
                },
            )
        return {"message": "saved", "title": title}
