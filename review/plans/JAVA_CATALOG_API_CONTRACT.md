# Java Catalog API Contract (Python consumer)

**Audience:** Java backend team + Python Phase 7 implementers  
**Base URL env:** `POLICY_CATALOG_URL` (e.g. `http://java-backend:9000/api/v1`)

Python already implements the consumer in `review_agent/clients/policy_catalog.py` (`HttpPolicyCatalogClient`). Phase 7 adds document-mcp `sync_policy_from_catalog` using the **same JSON shape**.

---

## 1. Get policy by ref (required)

```http
GET /api/v1/tenants/{tenant_id}/policies/{policy_ref}
```

- `policy_ref` URL-encoded opaque string
- Examples: `drive:1a2b3c`, `confluence:12345`, `sharepoint:item-uuid`

### 200 OK

```json
{
  "title": "Vendor Management Policy",
  "text": "Plain text body used for indexing...",
  "policy_type": "vendor_policy",
  "applies_to_contract_types": ["msa", "vendor"],
  "document_id": "550e8400-e29b-41d4-a716-446655440000",
  "metadata": {
    "source": "google_drive",
    "source_location": "Legal/Policies/Vendor Policy v7",
    "drive_file_id": "1a2b3c",
    "version": "7",
    "department": "Legal",
    "content_hash": "sha256hex...",
    "blob_uri": "s3://bucket/path/file.pdf"
  }
}
```

### 404 Not Found

Policy not in catalog for tenant.

---

## 2. Field rules

| Field | Required | Notes |
|-------|----------|-------|
| `title` | yes | Display + registry |
| `text` | yes for index | Extracted plain text; not PDF bytes |
| `policy_type` | no | Used in search filters |
| `applies_to_contract_types` | no | Default `[]` |
| `document_id` | recommended | Stable UUID; Python falls back to `uuid5(tenant:ref)` |
| `metadata.source` | recommended | `google_drive`, `confluence`, `sharepoint`, `upload` |
| `metadata.content_hash` | recommended | Skip re-embed when unchanged |
| `metadata.categories` | recommended (Phase 10) | String array e.g. `["liability","indemnity"]` for metadata-filter retrieval |
| `metadata.blob_uri` | optional | Audit; Python v1 does not fetch blob |

---

## 3. Java → document-mcp push (recommended)

After Java sync downloads file from any source:

```http
POST http://document-mcp:8002/tools/register_policy
Content-Type: application/json

{
  "tenant_id": "acme",
  "policy_ref": "drive:1a2b3c",
  "title": "Vendor Management Policy",
  "document_id": "550e8400-e29b-41d4-a716-446655440000",
  "policy_type": "vendor_policy",
  "applies_to_contract_types": ["msa"],
  "source": "google_drive",
  "metadata": {
    "source_location": "Legal/Policies/...",
    "version": "7",
    "content_hash": "sha256..."
  }
}
```

```http
POST http://document-mcp:8002/tools/index_policy
Content-Type: application/json

{
  "tenant_id": "acme",
  "document_id": "550e8400-e29b-41d4-a716-446655440000",
  "title": "Vendor Management Policy",
  "kind": "policy",
  "text": "...",
  "policy_type": "vendor_policy",
  "metadata": {
    "policy_ref": "drive:1a2b3c",
    "policy_title": "Vendor Management Policy",
    "source": "google_drive",
    "version": "7"
  }
}
```

---

## 4. policy_ref naming convention

| Source | Format | Example |
|--------|--------|---------|
| Google Drive | `drive:{file_id}` | `drive:1a2b3cDRIVEID` |
| Confluence | `confluence:{page_id}` | `confluence:12345678` |
| SharePoint | `sharepoint:{drive_item_id}` | `sharepoint:01ABC...` |
| Manual upload | `upload:{registry_id}` | `upload:doc-42` |

Prefix is opaque to Python — only used for stable UUID and display.

---

## 5. Index status mapping

| Java registry | Python `policy_documents.index_status` |
|---------------|----------------------------------------|
| DISCOVERED | — (not in Python yet) |
| SYNCED | `pending` (after register_policy) |
| INDEXED | `indexed` (after index_policy) |
| INDEX_FAILED | `failed` |

---

## 6. Review payload (Java → Python platform)

```json
{
  "query": "Review this MSA",
  "task_type": "review",
  "tenant_id": "acme",
  "thread_id": "session-uuid",
  "contract_text": "...",
  "policy_document_ids": ["uuid-p1", "uuid-p2"]
}
```

Or contract-only (policies already in pgvector):

```json
{
  "query": "Review this MSA",
  "task_type": "review",
  "tenant_id": "acme",
  "contract_text": "..."
}
```

With `REVIEW_POLICY_SOURCE=tenant_auto` on Python side.

---

*Aligns with `PolicyDocument` in `review_agent/clients/policy_catalog.py`.*
