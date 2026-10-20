"""Fixed-window counters for rate limits and quotas.

Production has no Redis (REDIS_URL is unset), so counters live in Postgres
(table ``rate_limits``, see migrations/2026-10-20_rate_limits.sql). Redis is
used instead when configured.

Fails OPEN: if the store is unreachable or the table does not exist yet, the
request is allowed and a warning is logged. These limits protect cost, not
data, so availability wins; the API Gateway throttle is the backstop.
"""
from __future__ import annotations

import logging

import psycopg2

from services.core.cache import get_redis
from services.core.settings import settings

log = logging.getLogger(__name__)

_UPSERT = """
INSERT INTO rate_limits (key, count, reset_at)
VALUES (%(key)s, 1, now() + make_interval(secs => %(window)s))
ON CONFLICT (key) DO UPDATE SET
    count    = CASE WHEN rate_limits.reset_at <= now() THEN 1 ELSE rate_limits.count + 1 END,
    reset_at = CASE WHEN rate_limits.reset_at <= now() THEN EXCLUDED.reset_at ELSE rate_limits.reset_at END
RETURNING count
"""


def _hit_postgres(key: str, window: int) -> int | None:
    try:
        conn = psycopg2.connect(settings.DATABASE_URL, connect_timeout=5)
        try:
            with conn, conn.cursor() as cur:
                cur.execute(_UPSERT, {"key": key, "window": window})
                return cur.fetchone()[0]
        finally:
            conn.close()
    except Exception:
        log.warning("rate limit store unavailable (key prefix=%s); allowing request", key.split(":")[0])
        return None


async def hit(key: str, limit: int, window: int) -> bool:
    """Count one hit on ``key``. Returns False once ``limit`` is exceeded within ``window`` seconds."""
    redis = await get_redis()
    if redis is not None:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, window)
    else:
        count = _hit_postgres(key, window)
        if count is None:
            return True
    return count <= limit
