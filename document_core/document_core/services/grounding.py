"""Quote verification for dual grounding."""

from __future__ import annotations

import re

from document_core.schemas.chunk import GroundingCheckRequest, GroundingCheckResult
from document_core.store.memory_store import get_store
from document_core.store.protocol import DocumentStore

_WS_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    return _WS_RE.sub(" ", text.strip().lower())


async def verify_quote(
    request: GroundingCheckRequest,
    *,
    store: DocumentStore | None = None,
) -> GroundingCheckResult:
    """Substring match on canonical or section text (fail closed)."""
    doc_store = store or get_store()
    quote_norm = normalize_text(request.quote)
    if not quote_norm:
        return GroundingCheckResult(
            grounded=False,
            quote=request.quote,
            normalized_quote=quote_norm,
            message="empty quote",
        )

    haystacks: list[tuple[str, str | None]] = []

    if request.section_id:
        parent = doc_store.get_parent_by_section(
            request.tenant_id,
            request.document_id,
            request.section_id,
        )
        if parent:
            haystacks.append((parent.text, parent.section_id))

    canonical = doc_store.get_canonical_text(request.tenant_id, request.document_id)
    if canonical:
        haystacks.append((canonical, request.section_id))

    for parents in (
        doc_store.get_parents(request.tenant_id, request.document_id),
    ):
        for parent in parents:
            haystacks.append((parent.text, parent.section_id))

    seen: set[str] = set()
    for text, section_id in haystacks:
        key = text[:80]
        if key in seen:
            continue
        seen.add(key)
        if quote_norm in normalize_text(text):
            return GroundingCheckResult(
                grounded=True,
                quote=request.quote,
                normalized_quote=quote_norm,
                section_id=section_id,
                message="quote found in source text",
            )

    return GroundingCheckResult(
        grounded=False,
        quote=request.quote,
        normalized_quote=quote_norm,
        section_id=request.section_id,
        message="quote not found in source text",
    )
