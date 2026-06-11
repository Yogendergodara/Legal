"""Local embedding generation via sentence-transformers."""

from __future__ import annotations

import asyncio
import time
from functools import lru_cache
from typing import Any

from mcp.retrieval_server.config import get_settings
from mcp.retrieval_server.logging_setup import get_logger, truncate

logger = get_logger(__name__)

_model: Any = None
_model_name: str | None = None


@lru_cache
def _get_model_name() -> str:
    return get_settings().embedding_model


def _load_model() -> Any:
    global _model, _model_name
    name = _get_model_name()
    if _model is not None and _model_name == name:
        return _model
    from sentence_transformers import SentenceTransformer

    logger.info("loading embedding model", model=name)
    _model = SentenceTransformer(name)
    _model_name = name
    return _model


def _embed_sync(texts: list[str]) -> list[list[float]]:
    model = _load_model()
    vectors = model.encode(texts, normalize_embeddings=True)
    return [v.tolist() for v in vectors]


async def embed_text(text: str) -> list[float]:
    """Embed a single text string."""
    start = time.perf_counter()
    result = await asyncio.to_thread(_embed_sync, [text])
    duration_ms = int((time.perf_counter() - start) * 1000)
    logger.debug(
        "embedding computed",
        text_len=len(text),
        duration_ms=duration_ms,
        preview=truncate(text, 50),
    )
    return result[0]


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed multiple texts in one batch."""
    if not texts:
        return []
    start = time.perf_counter()
    result = await asyncio.to_thread(_embed_sync, texts)
    duration_ms = int((time.perf_counter() - start) * 1000)
    logger.debug(
        "batch embedding computed",
        count=len(texts),
        duration_ms=duration_ms,
    )
    return result
