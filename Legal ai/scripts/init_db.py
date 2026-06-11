#!/usr/bin/env python3
"""Apply db/migrations/001_init.sql without needing psql installed locally.

Usage:
  python scripts/init_db.py
  DATABASE_URL=postgresql://legalai:legalai@localhost:5432/legalai python scripts/init_db.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "db" / "migrations" / "001_init.sql"


def main() -> None:
    database_url = os.getenv(
        "DATABASE_URL",
        "postgresql://legalai:legalai@localhost:5435/legalai",
    )
    if not MIGRATION.exists():
        print(f"Migration file not found: {MIGRATION}", file=sys.stderr)
        sys.exit(1)

    sql = MIGRATION.read_text(encoding="utf-8")
    print(f"Connecting to {database_url.split('@')[-1]} ...")
    try:
        conn = psycopg2.connect(database_url)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.close()
    except psycopg2.OperationalError as exc:
        print(f"Connection failed: {exc}", file=sys.stderr)
        print(
            "\nMake sure Postgres is running and reachable. Options:\n"
            "  1. docker compose up -d postgres   (with port 5432 mapped)\n"
            "  2. Or set DATABASE_URL to your Postgres host\n",
            file=sys.stderr,
        )
        sys.exit(1)

    print("Migration applied successfully.")


if __name__ == "__main__":
    main()
