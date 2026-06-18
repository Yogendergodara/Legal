-- ModernBERT-embed-base uses 768-d vectors (was 384 / MiniLM)
ALTER TABLE document_chunks DROP COLUMN IF EXISTS embedding;
ALTER TABLE document_chunks ADD COLUMN embedding vector(768);

DROP INDEX IF EXISTS ix_document_chunks_embedding;
CREATE INDEX IF NOT EXISTS ix_document_chunks_embedding
    ON document_chunks USING hnsw (embedding vector_cosine_ops);
