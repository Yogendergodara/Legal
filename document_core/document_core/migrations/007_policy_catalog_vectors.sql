-- Phase R0: one catalog embedding per policy for semantic routing
CREATE TABLE IF NOT EXISTS policy_catalog_vectors (
    tenant_id       TEXT NOT NULL,
    document_id     UUID NOT NULL,
    policy_ref      TEXT,
    profile_text    TEXT NOT NULL,
    embedding       vector(768),
    catalog_version INT NOT NULL DEFAULT 1,
    tsv             tsvector GENERATED ALWAYS AS (to_tsvector('english', profile_text)) STORED,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, document_id)
);

CREATE INDEX IF NOT EXISTS ix_policy_catalog_embedding
    ON policy_catalog_vectors USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS ix_policy_catalog_tsv
    ON policy_catalog_vectors USING GIN (tsv);
