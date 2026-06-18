-- document_core corpus: tenant policy registry + parent/child chunks (pgvector)
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS policy_documents (
    tenant_id                 TEXT NOT NULL,
    document_id               UUID NOT NULL,
    kind                      TEXT NOT NULL CHECK (kind IN ('contract', 'policy')),
    title                     TEXT NOT NULL,
    policy_type               TEXT,
    applies_to_contract_types TEXT[] NOT NULL DEFAULT '{}',
    policy_ref                TEXT,
    content_hash              TEXT NOT NULL,
    source                    TEXT NOT NULL DEFAULT 'upload',
    metadata                  JSONB NOT NULL DEFAULT '{}',
    indexed_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, document_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_policy_documents_ref
    ON policy_documents (tenant_id, policy_ref)
    WHERE policy_ref IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_policy_documents_tenant_kind
    ON policy_documents (tenant_id, kind);

CREATE TABLE IF NOT EXISTS document_canonical (
    tenant_id      TEXT NOT NULL,
    document_id    UUID NOT NULL,
    canonical_text TEXT NOT NULL,
    PRIMARY KEY (tenant_id, document_id)
);

CREATE TABLE IF NOT EXISTS document_chunks (
    id                          BIGSERIAL PRIMARY KEY,
    tenant_id                   TEXT NOT NULL,
    document_id                 UUID NOT NULL,
    chunk_id                    TEXT NOT NULL,
    chunk_role                  TEXT NOT NULL CHECK (chunk_role IN ('parent', 'child')),
    parent_id                   TEXT,
    section_id                  TEXT NOT NULL,
    section_path                TEXT,
    title                       TEXT,
    text                        TEXT NOT NULL,
    context_text                TEXT,
    kind                        TEXT NOT NULL,
    policy_type                 TEXT,
    applies_to_contract_types   TEXT[] NOT NULL DEFAULT '{}',
    embedding                   vector(768),
    tsv                         tsvector,
    metadata                    JSONB NOT NULL DEFAULT '{}',
    UNIQUE (tenant_id, document_id, chunk_id)
);

CREATE INDEX IF NOT EXISTS ix_document_chunks_parents
    ON document_chunks (tenant_id, document_id)
    WHERE chunk_role = 'parent';

CREATE INDEX IF NOT EXISTS ix_document_chunks_children
    ON document_chunks (tenant_id, document_id, kind)
    WHERE chunk_role = 'child';

CREATE INDEX IF NOT EXISTS ix_document_chunks_tsv
    ON document_chunks USING GIN (tsv);

CREATE INDEX IF NOT EXISTS ix_document_chunks_embedding
    ON document_chunks USING hnsw (embedding vector_cosine_ops);
