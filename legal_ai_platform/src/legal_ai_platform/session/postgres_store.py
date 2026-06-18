"""Postgres-backed session persistence (normal relational tables, not pgvector)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from legal_ai_platform.session.models import MatterSnapshot, SessionState, Turn


class SessionPostgresStore:
    """Session header + append-only turns in platform_sessions / platform_session_turns."""

    def __init__(self, database_url: str, *, load_limit: int = 500) -> None:
        self._engine: Engine = create_engine(database_url, future=True)
        self._load_limit = max(1, load_limit)

    def load(self, tenant_id: str, thread_id: str) -> SessionState | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT summary, matter
                    FROM platform_sessions
                    WHERE tenant_id = :tenant_id AND thread_id = :thread_id
                    """
                ),
                {"tenant_id": tenant_id, "thread_id": thread_id},
            ).mappings().first()
            if row is None:
                return None

            turn_rows = conn.execute(
                text(
                    """
                    SELECT role, content, agent, task_type, created_at
                    FROM platform_session_turns
                    WHERE tenant_id = :tenant_id AND thread_id = :thread_id
                    ORDER BY created_at ASC, id ASC
                    LIMIT :limit
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "thread_id": thread_id,
                    "limit": self._load_limit,
                },
            ).mappings().all()

        matter_data = row["matter"] or {}
        if isinstance(matter_data, str):
            matter_data = json.loads(matter_data)

        turns = [
            Turn(
                role=tr["role"],
                content=tr["content"],
                agent=tr["agent"],
                task_type=tr["task_type"],
                timestamp=_as_utc(tr["created_at"]),
            )
            for tr in turn_rows
        ]
        return SessionState(
            thread_id=thread_id,
            tenant_id=tenant_id,
            summary=row["summary"] or "",
            turns=turns,
            matter=MatterSnapshot.model_validate(matter_data),
        )

    def exists(self, tenant_id: str, thread_id: str) -> bool:
        with self._engine.connect() as conn:
            count = conn.execute(
                text(
                    """
                    SELECT 1 FROM platform_sessions
                    WHERE tenant_id = :tenant_id AND thread_id = :thread_id
                    """
                ),
                {"tenant_id": tenant_id, "thread_id": thread_id},
            ).scalar()
        return count is not None

    def save(self, state: SessionState) -> None:
        matter_json = json.dumps(state.matter.model_dump(mode="json"))
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO platform_sessions
                        (tenant_id, thread_id, summary, matter, updated_at)
                    VALUES
                        (:tenant_id, :thread_id, :summary, CAST(:matter AS jsonb), now())
                    ON CONFLICT (tenant_id, thread_id) DO UPDATE SET
                        summary = EXCLUDED.summary,
                        matter = EXCLUDED.matter,
                        updated_at = now()
                    """
                ),
                {
                    "tenant_id": state.tenant_id,
                    "thread_id": state.thread_id,
                    "summary": state.summary or "",
                    "matter": matter_json,
                },
            )
            existing_count = conn.execute(
                text(
                    """
                    SELECT COUNT(*) FROM platform_session_turns
                    WHERE tenant_id = :tenant_id AND thread_id = :thread_id
                    """
                ),
                {"tenant_id": state.tenant_id, "thread_id": state.thread_id},
            ).scalar_one()

            for turn in state.turns[existing_count:]:
                conn.execute(
                    text(
                        """
                        INSERT INTO platform_session_turns
                            (tenant_id, thread_id, role, content, agent, task_type, created_at)
                        VALUES
                            (:tenant_id, :thread_id, :role, :content, :agent, :task_type, :created_at)
                        """
                    ),
                    {
                        "tenant_id": state.tenant_id,
                        "thread_id": state.thread_id,
                        "role": turn.role,
                        "content": turn.content,
                        "agent": turn.agent,
                        "task_type": turn.task_type,
                        "created_at": turn.timestamp,
                    },
                )

    def delete(self, tenant_id: str, thread_id: str) -> bool:
        existed = self.exists(tenant_id, thread_id)
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    """
                    DELETE FROM platform_session_turns
                    WHERE tenant_id = :tenant_id AND thread_id = :thread_id
                    """
                ),
                {"tenant_id": tenant_id, "thread_id": thread_id},
            )
            conn.execute(
                text(
                    """
                    DELETE FROM platform_sessions
                    WHERE tenant_id = :tenant_id AND thread_id = :thread_id
                    """
                ),
                {"tenant_id": tenant_id, "thread_id": thread_id},
            )
        return existed


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
