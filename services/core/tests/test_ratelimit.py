"""services/core/ratelimit.py: Redis path, Postgres path, and fail-open."""
import asyncio
import unittest
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from services.core import ratelimit


def run(coro):
    return asyncio.run(coro)


class FakeRedis:
    def __init__(self):
        self.counts, self.ttls = {}, {}

    async def incr(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key, window):
        self.ttls[key] = window


class TestRedisPath(unittest.TestCase):
    def test_allows_up_to_limit_then_blocks(self):
        r = FakeRedis()
        with patch.object(ratelimit, "get_redis", AsyncMock(return_value=r)):
            results = [run(ratelimit.hit("anon-llm:1.2.3.4", 2, 60)) for _ in range(3)]
        self.assertEqual(results, [True, True, False])
        self.assertEqual(r.ttls["anon-llm:1.2.3.4"], 60)  # window set on first hit only

    def test_keys_are_independent(self):
        r = FakeRedis()
        with patch.object(ratelimit, "get_redis", AsyncMock(return_value=r)):
            run(ratelimit.hit("anon-llm:1.1.1.1", 1, 60))
            self.assertTrue(run(ratelimit.hit("anon-llm:2.2.2.2", 1, 60)))


class TestPostgresPath(unittest.TestCase):
    def _db(self, count):
        cur = MagicMock()
        cur.fetchone.return_value = (count,)
        conn = MagicMock()
        conn.cursor.return_value = cur

        @contextmanager
        def get_db():
            yield conn
        return get_db, conn, cur

    def test_uses_shared_connection_when_no_redis(self):
        get_db, conn, cur = self._db(1)
        with patch.object(ratelimit, "get_redis", AsyncMock(return_value=None)), \
             patch.object(ratelimit, "get_db", get_db):
            self.assertTrue(run(ratelimit.hit("k", 2, 60)))
        sql = cur.execute.call_args.args[0]
        self.assertIn("ON CONFLICT (key) DO UPDATE", sql)
        self.assertEqual(cur.execute.call_args.args[1], {"key": "k", "window": 60})
        conn.close.assert_not_called()  # reused across invocations, never closed per call

    def test_blocks_when_count_exceeds_limit(self):
        get_db, _, _ = self._db(3)
        with patch.object(ratelimit, "get_redis", AsyncMock(return_value=None)), \
             patch.object(ratelimit, "get_db", get_db):
            self.assertFalse(run(ratelimit.hit("k", 2, 60)))

    def test_count_reads_without_incrementing(self):
        get_db, _, cur = self._db(4)
        with patch.object(ratelimit, "get_redis", AsyncMock(return_value=None)), \
             patch.object(ratelimit, "get_db", get_db):
            self.assertEqual(run(ratelimit.count("k")), 4)
        self.assertTrue(cur.execute.call_args.args[0].lstrip().startswith("SELECT"))

    def test_fails_open_when_store_unavailable(self):
        # e.g. DB down, or the migration has not been applied yet
        @contextmanager
        def broken():
            raise Exception("relation does not exist")
            yield  # pragma: no cover
        with patch.object(ratelimit, "get_redis", AsyncMock(return_value=None)), \
             patch.object(ratelimit, "get_db", broken):
            self.assertTrue(run(ratelimit.hit("k", 0, 60)))
            self.assertEqual(run(ratelimit.count("k")), 0)


class TestSyncPath(unittest.TestCase):
    def test_hit_sync_counts_in_postgres(self):
        with patch.object(ratelimit, "_hit_postgres", side_effect=[1, 2, 3]):
            self.assertEqual([ratelimit.hit_sync("k", 2, 60) for _ in range(3)], [True, True, False])

    def test_hit_sync_fails_open(self):
        with patch.object(ratelimit, "_hit_postgres", return_value=None):
            self.assertTrue(ratelimit.hit_sync("k", 0, 60))


if __name__ == "__main__":
    unittest.main()
