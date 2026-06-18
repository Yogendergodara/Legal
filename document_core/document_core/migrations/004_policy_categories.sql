-- Phase 10: policy category metadata index (categories stored in policy_documents.metadata JSONB)
CREATE INDEX IF NOT EXISTS ix_policy_documents_metadata_categories
    ON policy_documents USING gin ((metadata->'categories'));
