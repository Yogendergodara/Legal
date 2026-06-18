-- Policy registry lifecycle: metadata-only rows before full index
ALTER TABLE policy_documents
  ADD COLUMN IF NOT EXISTS index_status TEXT NOT NULL DEFAULT 'indexed'
    CHECK (index_status IN ('pending', 'indexed', 'failed'));

ALTER TABLE policy_documents
  ALTER COLUMN content_hash DROP NOT NULL;

UPDATE policy_documents
  SET index_status = 'indexed'
  WHERE content_hash IS NOT NULL;
