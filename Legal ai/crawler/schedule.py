"""Celery beat schedule for per-seed crawl frequencies."""

from __future__ import annotations

from celery.schedules import crontab

from crawler.tasks import celery_app

celery_app.conf.beat_schedule = {
    "crawl-hourly-check": {
        "task": "crawler.crawl_all_due",
        "schedule": crontab(minute=0),  # every hour; task filters by per-seed frequency
    },
}
