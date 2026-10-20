-- Fixed-window counters for rate limits / anonymous quotas (services/core/ratelimit.py).
-- Production has no Redis, so these live in Postgres. Idempotent.
--   psql "$DATABASE_URL" -f migrations/2026-10-20_rate_limits.sql
CREATE TABLE IF NOT EXISTS rate_limits (
    key       TEXT PRIMARY KEY,
    count     INTEGER     NOT NULL,
    reset_at  TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS rate_limits_reset_at_idx ON rate_limits (reset_at);
-- Expired rows are harmless (reset on next hit). To prune occasionally:
--   DELETE FROM rate_limits WHERE reset_at < now() - interval '1 day';
