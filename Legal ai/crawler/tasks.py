"""Celery tasks for scheduled legal domain crawling."""

from __future__ import annotations

import logging
import subprocess
from datetime import datetime, timedelta, timezone

from celery import Celery
from sqlalchemy.orm import Session

from crawler.config import get_crawler_config
from db.models import SeedSource
from db.session import get_engine

logger = logging.getLogger(__name__)

_cfg = get_crawler_config()

celery_app = Celery("legal_crawler", broker=_cfg["celery_broker_url"])
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Kolkata",
    enable_utc=True,
)

import crawler.schedule  # noqa: F401, E402 — registers beat schedule

FREQUENCY_DELTAS = {
    "hourly": timedelta(hours=1),
    "daily": timedelta(days=1),
    "weekly": timedelta(weeks=1),
}


@celery_app.task(name="crawler.crawl_seed")
def crawl_seed(seed_id: int) -> dict:
    """Run Scrapy spider for a single seed source."""
    logger.info("crawl task started", extra={"seed_id": seed_id})
    result = subprocess.run(
        ["scrapy", "crawl", "legal", "-a", f"seed_id={seed_id}"],
        capture_output=True,
        text=True,
        timeout=3600,
    )
    if result.returncode != 0:
        logger.error(
            "crawl task failed",
            extra={"seed_id": seed_id, "stderr": result.stderr[:500]},
        )
        return {"seed_id": seed_id, "status": "failed"}

    engine = get_engine(_cfg["database_url"])
    with Session(engine) as session:
        seed = session.get(SeedSource, seed_id)
        if seed:
            seed.last_crawled_at = datetime.now(timezone.utc)
            session.commit()

    logger.info("crawl task finished", extra={"seed_id": seed_id})
    return {"seed_id": seed_id, "status": "ok"}


@celery_app.task(name="crawler.crawl_all_due")
def crawl_all_due() -> dict:
    """Enqueue crawl tasks for all seeds that are due based on crawl_frequency."""
    now = datetime.now(timezone.utc)
    engine = get_engine(_cfg["database_url"])
    due_ids: list[int] = []

    with Session(engine) as session:
        seeds = session.query(SeedSource).filter(SeedSource.active.is_(True)).all()
        for seed in seeds:
            delta = FREQUENCY_DELTAS.get(seed.crawl_frequency, timedelta(days=1))
            if seed.last_crawled_at is None or (now - seed.last_crawled_at) >= delta:
                due_ids.append(seed.id)

    logger.info("schedule tick", extra={"seeds_due": len(due_ids)})

    for seed_id in due_ids:
        crawl_seed.delay(seed_id)

    return {"seeds_due": len(due_ids), "enqueued": due_ids}
