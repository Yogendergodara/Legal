"""File-backed session persistence (implements SessionStore)."""

from __future__ import annotations

import json
from pathlib import Path

from legal_ai_platform.session.models import SessionState


class SessionFileStore:
    """One JSON file per session: {base}/{tenant_id}/{thread_id}/state.json"""

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir

    def _path(self, tenant_id: str, thread_id: str) -> Path:
        safe_tenant = tenant_id.replace("/", "_") or "default"
        safe_thread = thread_id.replace("/", "_")
        return self._base_dir / safe_tenant / safe_thread / "state.json"

    def load(self, tenant_id: str, thread_id: str) -> SessionState | None:
        path = self._path(tenant_id, thread_id)
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return SessionState.model_validate(data)

    def exists(self, tenant_id: str, thread_id: str) -> bool:
        return self._path(tenant_id, thread_id).is_file()

    def save(self, state: SessionState) -> None:
        path = self._path(state.tenant_id, state.thread_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            state.model_dump_json(indent=2),
            encoding="utf-8",
        )

    def delete(self, tenant_id: str, thread_id: str) -> bool:
        path = self._path(tenant_id, thread_id)
        if not path.is_file():
            return False
        path.unlink()
        thread_dir = path.parent
        if thread_dir.is_dir() and not any(thread_dir.iterdir()):
            thread_dir.rmdir()
            tenant_dir = thread_dir.parent
            if tenant_dir.is_dir() and not any(tenant_dir.iterdir()):
                tenant_dir.rmdir()
        return True
