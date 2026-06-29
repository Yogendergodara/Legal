#!/usr/bin/env python3
"""List all indexed policies in pgvector (all tenants)."""

from __future__ import annotations

import os
import sys

try:
    import psycopg2
except ImportError:
    import subprocess

    subprocess.check_call([sys.executable, "-m", "pip", "install", "psycopg2-binary", "-q"])
    import psycopg2

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://legalai:legalai@127.0.0.1:5435/legalai",
)


def main() -> int:
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT tenant_id, title, policy_ref, document_id::text, index_status
        FROM policy_documents
        WHERE kind = 'policy' AND index_status != 'deleted'
        ORDER BY tenant_id, title
        """
    )
    rows = cur.fetchall()
    print(f"TOTAL: {len(rows)} policies in database\n")

    last_tenant: str | None = None
    for tenant, title, ref, doc_id, status in rows:
        if tenant != last_tenant:
            print(f"\n--- tenant: {tenant} ---")
            last_tenant = tenant
        print(f"  • {title}")
        print(f"      policy_ref: {ref}")
        print(f"      document_id: {doc_id}")
        print(f"      status: {status}")

    cur.execute(
        """
        SELECT tenant_id, COUNT(*)
        FROM policy_documents
        WHERE kind = 'policy' AND index_status = 'indexed'
        GROUP BY tenant_id
        ORDER BY tenant_id
        """
    )
    print("\n\nTENANT IDs (use same tenant_id in Java + review):")
    for tenant, count in cur.fetchall():
        print(f"  {tenant} — {count} indexed policies")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
