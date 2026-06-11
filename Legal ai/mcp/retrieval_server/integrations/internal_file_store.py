"""File-based tenant document store for local dev without Postgres."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.retrieval_server.logging_setup import get_logger, truncate

logger = get_logger(__name__)

_DEFAULT_ROOT = Path(__file__).resolve().parents[3] / "data" / "internal_docs"


def _store_root(root: Path | None = None) -> Path:
    path = root or _DEFAULT_ROOT
    path.mkdir(parents=True, exist_ok=True)
    return path


def _tenant_dir(tenant_id: str, root: Path | None = None) -> Path:
    safe = re.sub(r"[^\w\-.]+", "_", tenant_id)
    path = _store_root(root) / safe
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_filename(source_id: str) -> str:
    """Make source_id safe for cross-platform filenames (Windows disallows ':')."""
    return re.sub(r"[^\w\-.]", "_", source_id)


def _doc_path(tenant_id: str, source_id: str, root: Path | None = None) -> Path:
    return _tenant_dir(tenant_id, root) / f"{_safe_filename(source_id)}.json"


def _load_doc(path: Path) -> dict[str, Any] | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("skipping unreadable internal doc file", path=str(path))
        return None


def ingest_document(
    *,
    tenant_id: str,
    title: str,
    doc_text: str,
    content_hash: str,
    source_id: str | None = None,
    metadata: dict | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Store a tenant document on disk. Dedupes by content_hash per tenant."""
    tenant_path = _tenant_dir(tenant_id, root)

    for existing_path in tenant_path.glob("*.json"):
        doc = _load_doc(existing_path)
        if doc and doc.get("content_hash") == content_hash:
            return {
                "tenant_id": tenant_id,
                "source_id": doc["source_id"],
                "title": doc["title"],
                "deduped": True,
            }

    doc_source_id = source_id or f"internal:{uuid.uuid4().hex[:12]}"
    record = {
        "tenant_id": tenant_id,
        "source_id": doc_source_id,
        "title": title,
        "clean_text": doc_text,
        "content_hash": content_hash,
        "metadata": metadata or {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _doc_path(tenant_id, doc_source_id, root).write_text(
        json.dumps(record, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(
        "file store ingest completed",
        tenant_id=tenant_id,
        source_id=doc_source_id,
    )
    return {
        "tenant_id": tenant_id,
        "source_id": doc_source_id,
        "title": title,
        "deduped": False,
    }


def get_document(
    tenant_id: str,
    source_id: str,
    root: Path | None = None,
) -> dict[str, Any] | None:
    """Load a tenant document from disk."""
    direct = _load_doc(_doc_path(tenant_id, source_id, root))
    if direct is not None:
        return direct
    for path in _tenant_dir(tenant_id, root).glob("*.json"):
        doc = _load_doc(path)
        if doc and doc.get("source_id") == source_id:
            return doc
    return None


def search_documents(
    query: str,
    tenant_id: str,
    max_results: int,
    root: Path | None = None,
) -> list[dict[str, Any]]:
    """Simple keyword search over stored tenant documents."""
    query_lower = query.lower()
    terms = [t for t in re.split(r"\W+", query_lower) if len(t) > 2]
    if not terms:
        terms = [query_lower.strip()] if query_lower.strip() else []

    tenant_path = _tenant_dir(tenant_id, root)
    scored: list[tuple[float, dict[str, Any]]] = []

    for path in tenant_path.glob("*.json"):
        doc = _load_doc(path)
        if not doc:
            continue
        haystack = f"{doc.get('title', '')} {doc.get('clean_text', '')}".lower()
        if not terms:
            continue
        hits = sum(1 for term in terms if term in haystack)
        if hits == 0:
            continue
        score = hits / len(terms)
        scored.append(
            (
                score,
                {
                    "source_id": doc["source_id"],
                    "title": doc["title"],
                    "text_snippet": truncate(doc.get("clean_text", ""), 200),
                    "score": round(score, 2),
                },
            )
        )

    scored.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in scored[:max_results]]
