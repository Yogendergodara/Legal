-- Phase 9: platform session chat + long-term memory (separate from document RAG)

CREATE TABLE IF NOT EXISTS platform_sessions (
    tenant_id     TEXT NOT NULL,
    thread_id     TEXT NOT NULL,
    summary       TEXT NOT NULL DEFAULT '',
    matter        JSONB NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, thread_id)
);

CREATE INDEX IF NOT EXISTS ix_platform_sessions_tenant_updated
    ON platform_sessions (tenant_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS platform_session_turns (
    id            BIGSERIAL PRIMARY KEY,
    tenant_id     TEXT NOT NULL,
    thread_id     TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content       TEXT NOT NULL,
    agent         TEXT,
    task_type     TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_platform_session_turns_thread
    ON platform_session_turns (tenant_id, thread_id, created_at);

CREATE TABLE IF NOT EXISTS platform_memory (
    id            BIGSERIAL PRIMARY KEY,
    tenant_id     TEXT NOT NULL,
    thread_id     TEXT,
    agent         TEXT NOT NULL,
    title         TEXT NOT NULL,
    content       TEXT NOT NULL,
    hook          TEXT NOT NULL DEFAULT '',
    embedding     vector(768),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_platform_memory_tenant
    ON platform_memory (tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_platform_memory_tsv
    ON platform_memory USING gin (to_tsvector('english', title || ' ' || content));
