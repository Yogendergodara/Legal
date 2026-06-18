"""Session persistence protocol — file or Postgres backends."""

from __future__ import annotations

from typing import Protocol

from legal_ai_platform.session.models import SessionState


class SessionStore(Protocol):
    """Load/save unified session state by tenant + thread."""

    def load(self, tenant_id: str, thread_id: str) -> SessionState | None: ...

    def save(self, state: SessionState) -> None: ...

    def exists(self, tenant_id: str, thread_id: str) -> bool: ...

    def delete(self, tenant_id: str, thread_id: str) -> bool: ...
