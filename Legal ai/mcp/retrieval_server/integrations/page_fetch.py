"""Fetch and extract clean text from web pages and PDFs."""



from __future__ import annotations



import time

from io import BytesIO

from typing import Any

from urllib.parse import unquote, urlparse



import httpx

import trafilatura

from pypdf import PdfReader



from mcp.retrieval_server.config import Settings

from mcp.retrieval_server.logging_setup import get_logger



logger = get_logger(__name__)



DEFAULT_USER_AGENT = "LegalAI-Fetcher/1.0 (contact@yourco.in)"





def _is_pdf_url(url: str) -> bool:

    path = urlparse(url).path.lower()

    return path.endswith(".pdf")





def _is_pdf_response(content_type: str, url: str) -> bool:

    lowered = content_type.lower()

    return "application/pdf" in lowered or _is_pdf_url(url)





def _title_from_url(url: str) -> str:

    path = urlparse(url).path

    name = unquote(path.rsplit("/", 1)[-1])

    if name.lower().endswith(".pdf"):

        name = name[:-4]

    return name.replace("-", " ").replace("_", " ").strip() or url





def _extract_pdf_text(content: bytes) -> tuple[str, str | None]:

    """Extract text and optional title from PDF bytes."""

    reader = PdfReader(BytesIO(content))

    title: str | None = None

    if reader.metadata:

        raw_title = reader.metadata.get("/Title") or reader.metadata.title

        if raw_title and str(raw_title).strip():

            title = str(raw_title).strip()



    pages: list[str] = []

    for page in reader.pages:

        pages.append(page.extract_text() or "")



    return "\n\n".join(part for part in pages if part.strip()), title





def _extract_html_text(html: str) -> tuple[str, str | None, str | None]:

    """Extract main text, title, and published date from HTML."""

    text = trafilatura.extract(html, include_comments=False) or ""

    meta = trafilatura.extract_metadata(html)

    title = getattr(meta, "title", None) if meta else None

    published = getattr(meta, "date", None) if meta else None

    return text, title, published





async def fetch_clean_text(

    url: str,

    request_id: str = "-",

    settings: Settings | None = None,

    http_client: httpx.AsyncClient | None = None,

) -> dict[str, Any]:

    """

    Fetch a URL and extract main text content from HTML or PDF.

    Returns {url, title, text, published, raw_html_len, content_type, extractor}.

    """

    from mcp.retrieval_server.config import get_settings



    cfg = settings or get_settings()

    user_agent = cfg.page_fetch_user_agent or DEFAULT_USER_AGENT

    timeout = cfg.external_timeout_seconds



    logger.info("fetching page", request_id=request_id, url=url)



    start = time.perf_counter()

    try:

        if http_client is not None:

            response = await http_client.get(

                url,

                headers={"User-Agent": user_agent, "Accept": "text/html,application/pdf,*/*"},

                follow_redirects=True,

                timeout=timeout,

            )

        else:

            async with httpx.AsyncClient(

                timeout=timeout,

                follow_redirects=True,

                headers={"User-Agent": user_agent, "Accept": "text/html,application/pdf,*/*"},

            ) as client:

                response = await client.get(url)



        content_type = response.headers.get("content-type", "")

        is_pdf = _is_pdf_response(content_type, str(response.url))



        if is_pdf:

            text, pdf_title = _extract_pdf_text(response.content)

            title = pdf_title or _title_from_url(str(response.url))

            published = None

            raw_len = len(response.content)

            extractor = "pdf"

        else:

            html = response.text

            text, title, published = _extract_html_text(html)

            raw_len = len(html)

            extractor = "html"



        duration_ms = int((time.perf_counter() - start) * 1000)



        logger.info(

            "page fetched",

            request_id=request_id,

            url=str(response.url),

            status=response.status_code,

            extractor=extractor,

            chars=len(text),

            duration_ms=duration_ms,

        )



        return {

            "url": str(response.url),

            "title": title,

            "text": text,

            "published": published,

            "raw_html_len": raw_len,

            "content_type": content_type,

            "extractor": extractor,

        }



    except httpx.TimeoutException:

        duration_ms = int((time.perf_counter() - start) * 1000)

        logger.warning(

            "page fetch timeout",

            request_id=request_id,

            url=url,

            duration_ms=duration_ms,

        )

        raise



    except Exception as exc:

        duration_ms = int((time.perf_counter() - start) * 1000)

        logger.error(

            "page fetch failed",

            request_id=request_id,

            url=url,

            error=type(exc).__name__,

            message=str(exc),

            duration_ms=duration_ms,

            exc_info=True,

        )

        raise


