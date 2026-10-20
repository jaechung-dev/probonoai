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

from services.core.cache import get_redis
from services.core.db import get_db

log = logging.getLogger(__name__)

_UPSERT = """
INSERT INTO rate_limits (key, count, reset_at)
VALUES (%(key)s, 1, now() + make_interval(secs => %(window)s))
ON CONFLICT (key) DO UPDATE SET
    count    = CASE WHEN rate_limits.reset_at <= now() THEN 1 ELSE rate_limits.count + 1 END,
    reset_at = CASE WHEN rate_limits.reset_at <= now() THEN EXCLUDED.reset_at ELSE rate_limits.reset_at END
RETURNING count
"""


# Uses the Lambda's long-lived connection (services/core/db.get_db). Opening a
# fresh connection per call costs ~1.5s from Sydney to the Singapore database,
# and a failed login makes several calls.
def _hit_postgres(key: str, window: int) -> int | None:
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(_UPSERT, {"key": key, "window": window})
            return cur.fetchone()[0]
    except Exception:
        log.warning("rate limit store unavailable (key prefix=%s); allowing request", key.split(":")[0])
        return None


async def count(key: str) -> int:
    """Current count for ``key`` in its active window, without incrementing. 0 if unknown/unavailable."""
    redis = await get_redis()
    if redis is not None:
        return int(await redis.get(key) or 0)
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT count FROM rate_limits WHERE key = %s AND reset_at > now()", (key,))
            row = cur.fetchone()
            return row[0] if row else 0
    except Exception:
        log.warning("rate limit store unavailable (key prefix=%s); allowing request", key.split(":")[0])
        return 0


def hit_sync(key: str, limit: int, window: int) -> bool:
    """``hit`` for sync (threadpool) endpoints. Always uses Postgres."""
    count = _hit_postgres(key, window)
    return True if count is None else count <= limit


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
