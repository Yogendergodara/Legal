"""Apply SQL migrations for document_core."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, text

_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def run_migrations(database_url: str) -> None:
    engine = create_engine(database_url, future=True)
    migration_files = sorted(_MIGRATIONS_DIR.glob("*.sql"))
    with engine.begin() as conn:
        for migration in migration_files:
            sql = migration.read_text(encoding="utf-8")
            conn.execute(text(sql))
