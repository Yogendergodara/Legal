"""Scrapy spider for legal domain crawling."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urljoin, urlparse

import scrapy
from scrapy.http import Response

from crawler.config import get_crawler_config
from crawler.extraction import extract_page
from crawler.storage import _get_embedding_sync, upload_raw_html_to_s3, upsert_document
from db.models import CrawlCache, SeedSource
from db.session import get_engine
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class LegalSpider(scrapy.Spider):
    """Crawls seed URLs from seed_sources registry."""

    name = "legal"
    custom_settings = {
        "ROBOTSTXT_OBEY": True,
        "DOWNLOAD_DELAY": 1,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
        "USER_AGENT": "LegalAI-Crawler/1.0 (+https://yourco.in/crawler; contact@yourco.in)",
        "HTTPERROR_ALLOW_ALL": True,
    }

    def __init__(self, seed_id: int | None = None, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.seed_id = int(seed_id) if seed_id else None
        self._cfg = get_crawler_config()
        self._engine = get_engine(self._cfg["database_url"])
        self._pages_fetched = 0
        self._pages_skipped = 0
        self._pages_failed = 0
        self._docs_upserted = 0
        self._docs_deduped = 0

    def _conditional_headers(self, url: str) -> dict[str, str]:
        headers: dict[str, str] = {}
        with Session(self._engine) as session:
            cache = session.query(CrawlCache).filter(CrawlCache.url == url).first()
            if cache:
                if cache.etag:
                    headers["If-None-Match"] = cache.etag
                if cache.last_modified:
                    headers["If-Modified-Since"] = cache.last_modified
        return headers

    def _update_cache(self, url: str, response: Response) -> None:
        etag = response.headers.get("ETag", b"").decode("utf-8", errors="ignore") or None
        last_mod = response.headers.get("Last-Modified", b"").decode("utf-8", errors="ignore") or None
        if not etag and not last_mod:
            return
        with Session(self._engine) as session:
            cache = session.query(CrawlCache).filter(CrawlCache.url == url).first()
            if cache:
                cache.etag = etag
                cache.last_modified = last_mod
            else:
                session.add(CrawlCache(url=url, etag=etag, last_modified=last_mod))
            session.commit()

    def start_requests(self):
        with Session(self._engine) as session:
            if self.seed_id:
                seeds = session.query(SeedSource).filter(
                    SeedSource.id == self.seed_id,
                    SeedSource.active.is_(True),
                ).all()
            else:
                seeds = session.query(SeedSource).filter(SeedSource.active.is_(True)).all()

        for seed in seeds:
            start_url = (
                seed.url_pattern
                if seed.url_pattern.startswith("http")
                else f"https://{seed.domain}{seed.url_pattern}"
            )
            logger.info(
                "crawl started",
                extra={"seed_id": seed.id, "domain": seed.domain, "url": start_url},
            )
            yield scrapy.Request(
                url=start_url,
                callback=self.parse,
                meta={"seed_id": seed.id, "domain": seed.domain},
                headers=self._conditional_headers(start_url),
                dont_filter=True,
            )

    def parse(self, response: Response):
        seed_id = response.meta.get("seed_id")
        url = response.url

        if response.status == 304:
            self._pages_skipped += 1
            logger.info(
                "page skipped unchanged",
                extra={"url": url, "seed_id": seed_id, "status": 304},
            )
            return

        if response.status != 200:
            self._pages_failed += 1
            logger.warning(
                "page failed",
                extra={"url": url, "seed_id": seed_id, "status": response.status},
            )
            return

        self._pages_fetched += 1
        self._update_cache(url, response)
        logger.info(
            "page fetched",
            extra={"url": url, "seed_id": seed_id, "status": response.status},
        )

        html = response.text
        extracted = extract_page(html, url)

        if not extracted["clean_text"]:
            logger.info("page empty after extraction", extra={"url": url})
            return

        upload_raw_html_to_s3(
            extracted["content_hash"],
            html,
            bucket=self._cfg["s3_bucket"],
            endpoint=self._cfg["s3_endpoint"],
            access_key=self._cfg["aws_access_key_id"],
            secret_key=self._cfg["aws_secret_access_key"],
        )

        embedding = _get_embedding_sync(extracted["clean_text"])

        with Session(self._engine) as session:
            _, deduped = upsert_document(
                session,
                url=url,
                canonical_url=extracted.get("canonical_url"),
                source_id=seed_id,
                title=extracted.get("title"),
                clean_text=extracted["clean_text"],
                content_hash=extracted["content_hash"],
                published_at=extracted.get("published_at"),
                embedding=embedding,
            )
            session.commit()
            if deduped:
                self._docs_deduped += 1
            else:
                self._docs_upserted += 1

        domain = response.meta.get("domain", urlparse(url).netloc)
        for href in response.css("a::attr(href)").getall():
            next_url = urljoin(url, href)
            if urlparse(next_url).netloc == domain:
                yield scrapy.Request(
                    url=next_url,
                    callback=self.parse,
                    meta={"seed_id": seed_id, "domain": domain},
                    headers=self._conditional_headers(next_url),
                )

    def closed(self, reason: str) -> None:
        logger.info(
            "crawl finished",
            extra={
                "reason": reason,
                "pages_fetched": self._pages_fetched,
                "pages_skipped": self._pages_skipped,
                "pages_failed": self._pages_failed,
                "docs_upserted": self._docs_upserted,
                "docs_deduped": self._docs_deduped,
            },
        )
