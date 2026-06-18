#!/usr/bin/env python3
"""Apply db/migrations/*.sql in order."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = ROOT / "db" / "migrations"


def main() -> None:
    database_url = os.getenv(
        "DATABASE_URL",
        "postgresql://legalai:legalai@localhost:5435/legalai",
    )
    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not migration_files:
        print(f"No migrations in {MIGRATIONS_DIR}", file=sys.stderr)
        sys.exit(1)

    print(f"Connecting to {database_url.split('@')[-1]} ...")
    try:
        conn = psycopg2.connect(database_url)
        conn.autocommit = True
        with conn.cursor() as cur:
            for migration in migration_files:
                print(f"Applying {migration.name} ...")
                cur.execute(migration.read_text(encoding="utf-8"))
        conn.close()
    except psycopg2.OperationalError as exc:
        print(f"Connection failed: {exc}", file=sys.stderr)
        sys.exit(1)

    print("Migrations applied successfully.")


if __name__ == "__main__":
    main()
