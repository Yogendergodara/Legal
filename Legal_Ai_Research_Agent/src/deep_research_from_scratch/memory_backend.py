"""Pluggable memory retrieval backend (the seam for pgvector / Qdrant).

The agents and graph never talk to a storage engine directly -- they go through
``get_memory_backend()``. Today the default is a fast, dependency-free
file/keyword backend. Swapping in semantic vector search later (pgvector or
Qdrant) is a drop-in: implement the same ``MemoryBackend`` interface and select
it with the ``MEMORY_BACKEND`` environment variable -- no changes to nodes,
tools, or prompts.

    # .env
    MEMORY_BACKEND=file        # default (keyword search over .md + transcripts)
    # MEMORY_BACKEND=pgvector  # semantic search in Postgres + pgvector (future)
    # MEMORY_BACKEND=qdrant    # semantic search in Qdrant (future)

Why a seam instead of committing to a vector DB now: the file backend keeps the
project runnable with zero infra, while this interface guarantees we can adopt
pgvector/Qdrant for scale + semantic recall without rewrites.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import List, Protocol

from deep_research_from_scratch.memory_tools import (
    ENTRYPOINT_NAME,
    get_auto_mem_path,
    load_transcript,
)


@dataclass
class MemoryHit:
    """A single retrieval result from a memory backend."""

    text: str
    source: str
    score: float = 0.0


class MemoryBackend(Protocol):
    """Retrieval interface shared by every backend (file, pgvector, qdrant)."""

    def search_longterm(self, query: str, k: int = 5) -> List[MemoryHit]:
        """Recall durable cross-session facts relevant to ``query``."""
        ...

    def search_session(self, session_id: str, query: str, k: int = 5) -> List[MemoryHit]:
        """Recall older turns within a session relevant to ``query``."""
        ...


def _keyword_score(haystack: str, terms: List[str]) -> int:
    """Cheap relevance score: number of query terms present in the text."""
    low = haystack.lower()
    return sum(1 for t in terms if t in low)


class FileMemoryBackend:
    """Default backend: keyword search over MEMORY.md files and JSONL transcripts.

    Fast and dependency-free. Good enough until memory volume or paraphrase
    recall justifies a vector store, at which point ``PgVectorMemoryBackend`` /
    ``QdrantMemoryBackend`` implement this same interface.
    """

    def _terms(self, query: str) -> List[str]:
        return [t for t in re.split(r"\s+", (query or "").lower()) if t]

    def search_longterm(self, query: str, k: int = 5) -> List[MemoryHit]:
        terms = self._terms(query)
        hits: List[MemoryHit] = []
        for md_file in sorted(get_auto_mem_path().glob("*.md")):
            if md_file.name == ENTRYPOINT_NAME:
                continue
            try:
                text = md_file.read_text(encoding="utf-8")
            except OSError:
                continue
            score = _keyword_score(text, terms) if terms else 1
            if score > 0:
                hits.append(MemoryHit(text=text.strip(), source=md_file.name, score=float(score)))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:k]

    def search_session(self, session_id: str, query: str, k: int = 5) -> List[MemoryHit]:
        terms = self._terms(query)
        if not terms:
            return []
        hits: List[MemoryHit] = []
        for entry in load_transcript(session_id):
            msg = entry.get("message", {})
            text = msg.get("content", "")
            if not isinstance(text, str) or not text.strip():
                continue
            score = _keyword_score(text, terms)
            if score > 0:
                role = msg.get("role", entry.get("type", "unknown"))
                hits.append(MemoryHit(text=f"[{role}] {text.strip()}", source=session_id, score=float(score)))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:k]


_BACKEND_SINGLETON: MemoryBackend | None = None


def get_memory_backend() -> MemoryBackend:
    """Return the configured memory backend (singleton).

    Selected via ``MEMORY_BACKEND`` (default ``"file"``). ``pgvector`` and
    ``qdrant`` are reserved for the semantic upgrade and raise a clear error
    until implemented, so the drop-in point is explicit.
    """
    global _BACKEND_SINGLETON
    if _BACKEND_SINGLETON is not None:
        return _BACKEND_SINGLETON

    choice = (os.environ.get("MEMORY_BACKEND") or "file").strip().lower()
    if choice == "file":
        _BACKEND_SINGLETON = FileMemoryBackend()
    elif choice in ("pgvector", "qdrant"):
        raise NotImplementedError(
            f"MEMORY_BACKEND='{choice}' is not implemented yet. Implement a "
            f"backend exposing search_longterm()/search_session() (use "
            f"model_config.get_embeddings() for vectors) and register it here."
        )
    else:
        raise ValueError(f"Unknown MEMORY_BACKEND='{choice}'. Use 'file', 'pgvector', or 'qdrant'.")

    return _BACKEND_SINGLETON


def format_hits(hits: List[MemoryHit], empty: str = "No relevant memories found.") -> str:
    """Render hits as a readable block for prompt injection."""
    if not hits:
        return empty
    return "\n\n".join(f"--- {h.source} ---\n{h.text}" for h in hits)
