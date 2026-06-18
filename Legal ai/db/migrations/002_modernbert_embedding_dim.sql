-- ModernBERT-embed-base: 768-d vectors (was 384 / MiniLM)
ALTER TABLE web_documents DROP COLUMN IF EXISTS embedding;
ALTER TABLE web_documents ADD COLUMN embedding vector(768);
DROP INDEX IF EXISTS ix_web_documents_embedding;
CREATE INDEX IF NOT EXISTS ix_web_documents_embedding ON web_documents USING hnsw(embedding vector_cosine_ops);

ALTER TABLE tenant_documents DROP COLUMN IF EXISTS embedding;
ALTER TABLE tenant_documents ADD COLUMN embedding vector(768);
DROP INDEX IF EXISTS ix_tenant_documents_embedding;
CREATE INDEX IF NOT EXISTS ix_tenant_documents_embedding ON tenant_documents USING hnsw(embedding vector_cosine_ops);
