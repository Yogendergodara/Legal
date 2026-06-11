-- Phase 2 schema: pgvector, FTS, tenant docs, citations, crawl cache
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS seed_sources (
    id SERIAL PRIMARY KEY,
    domain VARCHAR(255) NOT NULL,
    url_pattern VARCHAR(512) NOT NULL,
    category VARCHAR(50) NOT NULL,
    crawl_frequency VARCHAR(20) DEFAULT 'daily',
    robots_respected BOOLEAN DEFAULT TRUE,
    last_crawled_at TIMESTAMPTZ,
    active BOOLEAN DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS web_documents (
    id SERIAL PRIMARY KEY,
    url VARCHAR(2048) UNIQUE NOT NULL,
    canonical_url VARCHAR(2048),
    source_id INTEGER REFERENCES seed_sources(id),
    title VARCHAR(1024),
    clean_text TEXT,
    content_hash VARCHAR(64) NOT NULL,
    published_at TIMESTAMPTZ,
    crawled_at TIMESTAMPTZ DEFAULT NOW(),
    tsv TSVECTOR,
    embedding vector(384)
);
CREATE INDEX IF NOT EXISTS ix_web_documents_content_hash ON web_documents(content_hash);
CREATE INDEX IF NOT EXISTS ix_web_documents_tsv ON web_documents USING gin(tsv);
CREATE INDEX IF NOT EXISTS ix_web_documents_embedding ON web_documents USING hnsw(embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS tenant_documents (
    id SERIAL PRIMARY KEY,
    tenant_id VARCHAR(128) NOT NULL,
    source_id VARCHAR(512) NOT NULL,
    title VARCHAR(1024) NOT NULL,
    clean_text TEXT NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    tsv TSVECTOR,
    embedding vector(384),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, source_id)
);
CREATE INDEX IF NOT EXISTS ix_tenant_documents_tenant ON tenant_documents(tenant_id);
CREATE INDEX IF NOT EXISTS ix_tenant_documents_tsv ON tenant_documents USING gin(tsv);
CREATE INDEX IF NOT EXISTS ix_tenant_documents_embedding ON tenant_documents USING hnsw(embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS citation_edges (
    id SERIAL PRIMARY KEY,
    from_source_id VARCHAR(512) NOT NULL,
    to_source_id VARCHAR(512) NOT NULL,
    from_source_type VARCHAR(50) NOT NULL,
    to_source_type VARCHAR(50) NOT NULL,
    citation_type VARCHAR(50) DEFAULT 'cites',
    metadata JSONB DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_citation_from ON citation_edges(from_source_id);
CREATE INDEX IF NOT EXISTS ix_citation_to ON citation_edges(to_source_id);

CREATE TABLE IF NOT EXISTS crawl_cache (
    id SERIAL PRIMARY KEY,
    url VARCHAR(2048) UNIQUE NOT NULL,
    etag VARCHAR(256),
    last_modified VARCHAR(128),
    fetched_at TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO seed_sources (domain, url_pattern, category, crawl_frequency, robots_respected, active)
VALUES
    ('livelaw.in', 'https://www.livelaw.in/', 'news', 'daily', true, false),
    ('barandbench.com', 'https://www.barandbench.com/', 'news', 'daily', true, false),
    ('prsindia.org', 'https://prsindia.org/', 'statute', 'weekly', true, false),
    ('egazette.gov.in', 'https://egazette.gov.in/', 'regulator', 'daily', true, false),
    ('sci.gov.in', 'https://main.sci.gov.in/', 'court', 'weekly', true, false)
ON CONFLICT DO NOTHING;
