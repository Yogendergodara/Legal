"""Local embedding generation via sentence-transformers."""

from __future__ import annotations

import asyncio
import time
from functools import lru_cache
from typing import Any, Literal

from mcp.retrieval_server.config import get_settings
from mcp.retrieval_server.logging_setup import get_logger, truncate

logger = get_logger(__name__)

InputType = Literal["query", "document"]

_model: Any = None
_model_name: str | None = None
_model_truncate_dim: int | None = None


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


@lru_cache
def _get_model_name() -> str:
    return get_settings().embedding_model


def _get_truncate_dim() -> int | None:
    return get_settings().embedding_truncate_dim or None


def _load_model() -> Any:
    global _model, _model_name, _model_truncate_dim
    name = _get_model_name()
    truncate_dim = _get_truncate_dim()
    if _model is not None and _model_name == name and _model_truncate_dim == truncate_dim:
        return _model
    from sentence_transformers import SentenceTransformer

    kwargs: dict = {}
    if truncate_dim is not None:
        kwargs["truncate_dim"] = truncate_dim
    logger.info("loading embedding model", model=name, truncate_dim=truncate_dim)
    _model = SentenceTransformer(name, **kwargs)
    _model_name = name
    _model_truncate_dim = truncate_dim
    return _model


def _embed_sync(texts: list[str], *, input_type: InputType = "document") -> list[list[float]]:
    model = _load_model()
    model_name = _get_model_name()
    prepared = _prepare_texts(texts, input_type, model_name)
    vectors = model.encode(prepared, normalize_embeddings=True)
    return [v.tolist() for v in vectors]


async def embed_text(text: str, *, input_type: InputType = "document") -> list[float]:
    """Embed a single text string."""
    start = time.perf_counter()
    result = await asyncio.to_thread(_embed_sync, [text], input_type=input_type)
    duration_ms = int((time.perf_counter() - start) * 1000)
    logger.debug(
        "embedding computed",
        text_len=len(text),
        duration_ms=duration_ms,
        preview=truncate(text, 50),
        input_type=input_type,
    )
    return result[0]


async def embed_query(text: str) -> list[float]:
    return await embed_text(text, input_type="query")


async def embed_batch(texts: list[str], *, input_type: InputType = "document") -> list[list[float]]:
    """Embed multiple texts in one batch."""
    if not texts:
        return []
    start = time.perf_counter()
    result = await asyncio.to_thread(_embed_sync, texts, input_type=input_type)
    duration_ms = int((time.perf_counter() - start) * 1000)
    logger.debug(
        "batch embedding computed",
        count=len(texts),
        duration_ms=duration_ms,
        input_type=input_type,
    )
    return result
