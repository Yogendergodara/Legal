"""Crawler worker configuration."""

from __future__ import annotations

import os
from functools import lru_cache


@lru_cache
def get_crawler_config() -> dict[str, str]:
    return {
        "database_url": os.getenv(
            "DATABASE_URL",
            "postgresql://legalai:legalai@postgres:5432/legalai",
        ),
        "celery_broker_url": os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0"),
        "s3_bucket": os.getenv("S3_BUCKET", "legalai-crawler"),
        "s3_endpoint": os.getenv("S3_ENDPOINT", ""),
        "aws_access_key_id": os.getenv("AWS_ACCESS_KEY_ID", ""),
        "aws_secret_access_key": os.getenv("AWS_SECRET_ACCESS_KEY", ""),
    }
