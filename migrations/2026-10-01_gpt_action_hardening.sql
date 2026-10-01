-- GPT Action hardening — safe to run more than once. Run BEFORE deploying the new API code.

-- 3. Refresh-token rotation chains + reuse detection (web sessions)
ALTER TABLE refresh_tokens ADD COLUMN IF NOT EXISTS family_id UUID;
ALTER TABLE refresh_tokens ADD COLUMN IF NOT EXISTS used_at   TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS refresh_tokens_family_idx ON refresh_tokens(family_id);

-- 1. OAuth 2.0 (ChatGPT Custom GPT Action)
CREATE TABLE IF NOT EXISTS oauth_auth_codes (
    code_hash      TEXT PRIMARY KEY,
    user_id        UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    client_id      TEXT NOT NULL,
    redirect_uri   TEXT NOT NULL,
    scopes         TEXT[] NOT NULL,
    code_challenge TEXT,
    expires_at     TIMESTAMPTZ NOT NULL,
    created_at     TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS oauth_auth_codes_expires_idx ON oauth_auth_codes(expires_at);

CREATE TABLE IF NOT EXISTS oauth_grants (          -- one row per "ChatGPT is connected" consent
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    client_id    TEXT NOT NULL,
    scopes       TEXT[] NOT NULL,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    last_used_at TIMESTAMPTZ,
    revoked_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS oauth_grants_user_idx ON oauth_grants(user_id);

CREATE TABLE IF NOT EXISTS oauth_refresh_tokens (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    family_id  UUID NOT NULL REFERENCES oauth_grants(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at    TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS oauth_refresh_tokens_family_idx ON oauth_refresh_tokens(family_id);

-- 6. Security event log (also emitted to logs as SECURITY_ALERT lines)
CREATE TABLE IF NOT EXISTS security_events (
    id         BIGSERIAL PRIMARY KEY,
    user_id    UUID,
    kind       TEXT NOT NULL,
    ip         TEXT,
    detail     JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS security_events_user_idx ON security_events(user_id, created_at DESC);

-- 4. Clamp existing long-lived MCP tokens to at most 90 days from now
UPDATE mcp_tokens SET expires_at = now() + interval '90 days'
 WHERE expires_at > now() + interval '90 days';

-- 7. users.salt is no longer written for new/upgraded (bcrypt) hashes — nothing to migrate;
--    legacy SHA-256 hashes upgrade to bcrypt automatically on each user's next login.
