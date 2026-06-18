"""Embedding generation for document chunk indexing (optional dependency)."""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Literal

from document_core.config import get_settings

logger = logging.getLogger(__name__)

InputType = Literal["query", "document"]


def _uses_search_prefixes(model_name: str) -> bool:
    lowered = model_name.lower()
    return "modernbert-embed" in lowered or "nomic-embed" in lowered


def _prefix_text(text: str, input_type: InputType) -> str:
    if input_type == "query":
        return f"search_query: {text}"
    return f"search_document: {text}"


def _prepare_texts(texts: list[str], input_type: InputType, model_name: str) -> list[str]:
    if not _uses_search_prefixes(model_name):
        return texts
    return [_prefix_text(text, input_type) for text in texts]


@lru_cache(maxsize=1)
def _load_model(model_name: str, truncate_dim: int | None):
    from sentence_transformers import SentenceTransformer

    kwargs: dict = {}
    if truncate_dim is not None:
        kwargs["truncate_dim"] = truncate_dim
    logger.info("loading embedding model: %s truncate_dim=%s", model_name, truncate_dim)
    return SentenceTransformer(model_name, **kwargs)


def embeddings_available() -> bool:
    settings = get_settings()
    if not settings.embedding_enabled:
        return False
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        return False
    return True


def embed_texts(
    texts: list[str],
    *,
    input_type: InputType = "document",
) -> list[list[float]] | None:
    """Return normalized embedding vectors or None if embeddings unavailable."""
    if not texts:
        return []
    settings = get_settings()
    if not settings.embedding_enabled:
        return None
    try:
        truncate_dim = settings.embedding_truncate_dim or None
        model = _load_model(settings.embedding_model, truncate_dim)
        prepared = _prepare_texts(texts, input_type, settings.embedding_model)
        vectors = model.encode(prepared, normalize_embeddings=True)
        return [vector.tolist() for vector in vectors]
    except Exception as exc:  # noqa: BLE001
        logger.warning("embedding failed: %s", exc)
        return None


def embed_query(text: str) -> list[float] | None:
    batch = embed_texts([text], input_type="query")
    if batch is None:
        return None
    return batch[0]


def embed_documents(texts: list[str]) -> list[list[float]] | None:
    return embed_texts(texts, input_type="document")


def embed_text(text: str) -> list[float] | None:
    batch = embed_documents([text])
    if batch is None:
        return None
    return batch[0]
