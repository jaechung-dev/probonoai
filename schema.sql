-- PostgreSQL + pgvector schema for probonoai
-- Run once: psql $DATABASE_URL < schema.sql

CREATE EXTENSION IF NOT EXISTS vector;

-- Case timeline events
CREATE TABLE IF NOT EXISTS case_events (
    id           SERIAL PRIMARY KEY,
    user_id      UUID REFERENCES users(id) ON DELETE CASCADE,
    case_id      TEXT NOT NULL,
    date         DATE NOT NULL,
    category     TEXT,
    event_type   TEXT,
    subject      TEXT,
    summary      TEXT,
    content      TEXT,
    attachments  JSONB DEFAULT '[]',
    embedding    vector(1536)
);
CREATE INDEX IF NOT EXISTS case_events_case_id_idx ON case_events (case_id);
CREATE INDEX IF NOT EXISTS case_events_user_case_idx ON case_events (user_id, case_id);
CREATE INDEX IF NOT EXISTS case_events_embedding_idx ON case_events
    USING hnsw (embedding vector_cosine_ops);

-- Legislation chunks
CREATE TABLE IF NOT EXISTS legislation_chunks (
    id           SERIAL PRIMARY KEY,
    citation     TEXT,
    jurisdiction TEXT DEFAULT 'NSW',
    text         TEXT,
    embedding    vector(1536)
);
CREATE INDEX IF NOT EXISTS legislation_chunks_embedding_idx ON legislation_chunks
    USING hnsw (embedding vector_cosine_ops);

-- Caselaw chunks
CREATE TABLE IF NOT EXISTS caselaw_chunks (
    id               SERIAL PRIMARY KEY,
    neutral_citation TEXT,
    title            TEXT,
    text             TEXT,
    embedding        vector(1536)
);
CREATE INDEX IF NOT EXISTS caselaw_chunks_embedding_idx ON caselaw_chunks
    USING hnsw (embedding vector_cosine_ops);

-- ── Auth tables ───────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS users (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email          TEXT UNIQUE NOT NULL,
    name           TEXT NOT NULL,
    password_hash  TEXT,
    salt           TEXT,
    role           TEXT NOT NULL DEFAULT 'user',
    provider       TEXT NOT NULL DEFAULT 'local',
    email_verified BOOLEAN NOT NULL DEFAULT false,
    created_at     TIMESTAMPTZ DEFAULT NOW(),
    updated_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS refresh_tokens (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  TEXT NOT NULL UNIQUE,
    expires_at  TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS refresh_tokens_hash_idx    ON refresh_tokens(token_hash);
CREATE INDEX IF NOT EXISTS refresh_tokens_user_idx    ON refresh_tokens(user_id);
CREATE INDEX IF NOT EXISTS refresh_tokens_expires_idx ON refresh_tokens(expires_at);

CREATE TABLE IF NOT EXISTS email_verifications (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  TEXT NOT NULL UNIQUE,
    expires_at  TIMESTAMPTZ NOT NULL,
    used        BOOLEAN NOT NULL DEFAULT false,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS password_resets (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  TEXT NOT NULL UNIQUE,
    expires_at  TIMESTAMPTZ NOT NULL,
    used        BOOLEAN NOT NULL DEFAULT false,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Google OAuth CSRF state — persisted so it survives across Lambda instances.
CREATE TABLE IF NOT EXISTS oauth_states (
    state       TEXT PRIMARY KEY,
    expires_at  TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS oauth_states_expires_idx ON oauth_states (expires_at);

-- Site-usage / sign-in audit log (who logged in, when, from where).
CREATE TABLE IF NOT EXISTS access_logs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     TEXT,
    email       TEXT,
    method      TEXT,
    ip          TEXT,
    user_agent  TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS access_logs_created_idx ON access_logs (created_at DESC);

-- ── Case document chunks (user-uploaded, RAG-indexed) ────────────────────────

CREATE TABLE IF NOT EXISTS case_chunks (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    case_id    UUID,
    user_id    TEXT NOT NULL,
    content    TEXT NOT NULL,
    embedding  vector(1536),
    metadata   JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS case_chunks_case_id_idx   ON case_chunks (case_id);
CREATE INDEX IF NOT EXISTS case_chunks_user_id_idx   ON case_chunks (user_id);
CREATE INDEX IF NOT EXISTS case_chunks_embedding_idx ON case_chunks
    USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=64);

-- ── Case intakes (intake form submissions) ────────────────────────────────────

CREATE TABLE IF NOT EXISTS case_intakes (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    submitted_at TIMESTAMPTZ DEFAULT NOW(),
    user_id      TEXT,
    personal     JSONB NOT NULL,
    matter       JSONB NOT NULL,
    files        JSONB NOT NULL DEFAULT '[]'
    -- files: [{ name, size, category, key }] — key is the S3 object path
);
CREATE INDEX IF NOT EXISTS case_intakes_user_id_idx    ON case_intakes (user_id);
CREATE INDEX IF NOT EXISTS case_intakes_created_at_idx ON case_intakes (created_at DESC);

-- ── Conversations ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS conversations (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    TEXT NOT NULL,
    title      TEXT NOT NULL DEFAULT 'New conversation',
    case_id    TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_conversations_user_updated ON conversations (user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS conversation_messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    sources         JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_conv_messages_conv_id ON conversation_messages (conversation_id, created_at);

-- ── Demo case events (seeded fixture data, mirrors case_events) ───────────────

CREATE TABLE IF NOT EXISTS demo_case_events (
    id          SERIAL PRIMARY KEY,
    case_id     TEXT NOT NULL,
    date        DATE NOT NULL,
    category    TEXT,
    event_type  TEXT,
    subject     TEXT,
    summary     TEXT,
    content     TEXT,
    attachments JSONB DEFAULT '[]',
    embedding   vector(1536)
);
CREATE INDEX IF NOT EXISTS demo_case_events_case_id_idx   ON demo_case_events (case_id);
CREATE INDEX IF NOT EXISTS demo_case_events_embedding_idx ON demo_case_events
    USING hnsw (embedding vector_cosine_ops);

-- ── Request / audit logs ──────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS request_logs (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    endpoint   TEXT NOT NULL,
    user_id    TEXT NOT NULL DEFAULT 'anon',
    input      JSONB NOT NULL,
    output     JSONB NOT NULL,
    elapsed_ms INTEGER
);
CREATE INDEX IF NOT EXISTS request_logs_user_id_idx    ON request_logs (user_id);
CREATE INDEX IF NOT EXISTS request_logs_created_at_idx ON request_logs (created_at DESC);
CREATE INDEX IF NOT EXISTS request_logs_endpoint_idx   ON request_logs (endpoint);

-- ── MCP tokens ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS mcp_tokens (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash   TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL DEFAULT 'My MCP Token',
    scopes       TEXT[] NOT NULL DEFAULT ARRAY['search','ask','fetch','collections'],
    expires_at   TIMESTAMPTZ NOT NULL,
    last_used_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS mcp_tokens_hash_idx    ON mcp_tokens(token_hash);
CREATE INDEX IF NOT EXISTS mcp_tokens_user_idx    ON mcp_tokens(user_id);
CREATE INDEX IF NOT EXISTS mcp_tokens_expires_idx ON mcp_tokens(expires_at);
