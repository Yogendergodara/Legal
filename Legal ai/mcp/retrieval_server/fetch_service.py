"""Fetch and extract full document content."""

from __future__ import annotations

import time

import httpx

from pathlib import Path

from db.models import TenantDocument
from db.session import get_session
from mcp.retrieval_server.config import Settings
from mcp.retrieval_server.integrations import internal_file_store
from mcp.retrieval_server.integrations.page_fetch import fetch_clean_text
from mcp.retrieval_server.logging_setup import get_logger
from mcp.retrieval_server.models import ExtractedSection, FetchRequest, FetchResponse

logger = get_logger(__name__)


class FetchService:
    """Fetch full documents and extract requested sections."""

    def __init__(
        self,
        http_client: httpx.AsyncClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._http_client = http_client
        self._settings = settings
        self._file_root = (
            Path(settings.internal_storage_dir)
            if settings and settings.internal_storage_dir
            else None
        )

    async def fetch_and_extract(
        self, request: FetchRequest, request_id: str
    ) -> FetchResponse:
        start = time.perf_counter()

        logger.info(
            "fetch started",
            source_id=request.source_id,
            source_type=request.source_type,
            extract_sections=request.extract_sections,
            tenant_id=request.tenant_id,
        )

        if request.source_type == "web":
            return await self._fetch_web(request, request_id, start)

        if request.source_type == "internal":
            return await self._fetch_internal(request, request_id, start)

        return self._placeholder_response(request, request_id, start)

    async def _fetch_internal(
        self, request: FetchRequest, request_id: str, start: float
    ) -> FetchResponse:
        if not request.tenant_id:
            raise ValueError("tenant_id required for internal document fetch")

        if self._settings and self._settings.internal_storage == "file":
            doc = internal_file_store.get_document(
                request.tenant_id,
                request.source_id,
                root=self._file_root,
            )
            if not doc:
                return self._placeholder_response(request, request_id, start, degraded=True)

            sections = [
                ExtractedSection(
                    section_id="full_text",
                    title="Full Text",
                    content=doc["clean_text"],
                )
            ]
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            return FetchResponse(
                request_id=request_id,
                source_id=doc["source_id"],
                source_type="internal",
                title=doc["title"],
                full_text=doc["clean_text"],
                sections=sections,
                url="",
                metadata={
                    "backend": "internal_file_store",
                    "tenant_id": request.tenant_id,
                },
                fetch_time_ms=elapsed_ms,
            )

        with get_session(self._settings.database_url) as session:  # type: ignore[union-attr]
            doc = session.query(TenantDocument).filter(
                TenantDocument.tenant_id == request.tenant_id,
                TenantDocument.source_id == request.source_id,
            ).first()

        if not doc:
            return self._placeholder_response(request, request_id, start, degraded=True)

        sections = [
            ExtractedSection(section_id="full_text", title="Full Text", content=doc.clean_text)
        ]
        elapsed_ms = int((time.perf_counter() - start) * 1000)

        return FetchResponse(
            request_id=request_id,
            source_id=doc.source_id,
            source_type="internal",
            title=doc.title,
            full_text=doc.clean_text,
            sections=sections,
            url="",
            metadata={"backend": "tenant_documents", "tenant_id": request.tenant_id},
            fetch_time_ms=elapsed_ms,
        )

    async def _fetch_web(
        self, request: FetchRequest, request_id: str, start: float
    ) -> FetchResponse:
        url = request.source_id
        if not url.startswith("http"):
            url = f"https://{request.source_id}"

        page = await fetch_clean_text(
            url,
            request_id=request_id,
            settings=self._settings,
            http_client=self._http_client,
        )

        full_text = page.get("text", "")
        title = page.get("title") or f"Web page {url}"
        sections_to_extract = request.extract_sections or ["full_text"]
        extracted = [
            ExtractedSection(
                section_id=sid,
                title=sid.replace("_", " ").title(),
                content=full_text if sid == "full_text" else "",
            )
            for sid in sections_to_extract
        ]

        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return FetchResponse(
            request_id=request_id,
            source_id=request.source_id,
            source_type=request.source_type,
            title=title,
            full_text=full_text,
            sections=extracted,
            url=page.get("url", url),
            metadata={"backend": "page_fetch"},
            fetch_time_ms=elapsed_ms,
        )

    def _placeholder_response(
        self,
        request: FetchRequest,
        request_id: str,
        start: float,
        degraded: bool = False,
    ) -> FetchResponse:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return FetchResponse(
            request_id=request_id,
            source_id=request.source_id,
            source_type=request.source_type,
            title=f"Document {request.source_id}",
            full_text=f"Placeholder for {request.source_id}",
            sections=[ExtractedSection(section_id="summary", title="Summary", content="Unavailable")],
            url=request.source_id if request.source_type == "web" else "",
            metadata={"degraded": degraded, "placeholder": True},
            fetch_time_ms=elapsed_ms,
        )
