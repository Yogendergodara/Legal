"""Process-wide document store (PostgreSQL + pgvector)."""

from __future__ import annotations

from document_core.store.protocol import DocumentStore

_active_store: DocumentStore | None = None


def get_store() -> DocumentStore:
    if _active_store is not None:
        return _active_store

    from document_core.config import get_settings
    from document_core.db.migrate import run_migrations
    from document_core.store.pgvector_store import PgVectorDocumentStore

    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError(
            "DATABASE_URL is required. Set DOCUMENT_STORE_BACKEND=pgvector and DATABASE_URL."
        )
    run_migrations(settings.database_url)
    return PgVectorDocumentStore(
        settings.database_url,
        hybrid_alpha=settings.search_hybrid_alpha,
    )


def set_store(store: DocumentStore) -> None:
    global _active_store  # noqa: PLW0603
    _active_store = store


def reset_store() -> None:
    global _active_store  # noqa: PLW0603
    _active_store = None
