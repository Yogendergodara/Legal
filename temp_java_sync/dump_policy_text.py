#!/usr/bin/env python3
"""Dump indexed policy titles and text from pgvector."""

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

TENANT = sys.argv[1] if len(sys.argv) > 1 else "z8rQswAUiHiO"


def main() -> int:
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT pd.tenant_id, pd.title, pd.policy_ref, pd.document_id::text, pd.index_status
        FROM policy_documents pd
        WHERE pd.kind = 'policy' AND pd.index_status != 'deleted'
          AND (%s = '' OR pd.tenant_id = %s)
        ORDER BY pd.tenant_id, pd.title
        """,
        (TENANT, TENANT),
    )
    policies = cur.fetchall()
    if not policies:
        print(f"No policies for tenant: {TENANT!r}")
        return 1

    for tenant, title, policy_ref, doc_id, status in policies:
        print("=" * 72)
        print(f"TENANT: {tenant}")
        print(f"TITLE:   {title}")
        print(f"REF:     {policy_ref}")
        print(f"DOC ID:  {doc_id}")
        print(f"STATUS:  {status}")
        print("-" * 72)
        cur.execute(
            """
            SELECT canonical_text FROM document_canonical
            WHERE tenant_id = %s AND document_id = %s::uuid
            """,
            (tenant, doc_id),
        )
        row = cur.fetchone()
        if row and row[0]:
            print(row[0].strip())
            print()
            continue

        cur.execute(
            """
            SELECT chunk_role, section_id, title, text
            FROM document_chunks
            WHERE tenant_id = %s AND document_id = %s::uuid AND chunk_role = 'parent'
            ORDER BY section_id
            """,
            (tenant, doc_id),
        )
        chunks = cur.fetchall()
        if not chunks:
            print("(no text found)")
            print()
            continue
        print("\n\n".join((c[3] or "").strip() for c in chunks if (c[3] or "").strip()))
        print()

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
